"""执行期校验冒烟测试 — 不需要 API Key。

三段里只有第一段要连库（词表是从库里读的），连不上就跳过它、其余照跑：
校验本身读不到词表也是这个行为，这里跟它保持一致。

重点覆盖两条容易写错的地方：
  - 第二段必须挂第一段：结构已命中说明查询本身不成立，不该再记一次空集。
  - employee_name 是开放集，不在词表里，不能被判成「不存在的取值」。

用法：
    python scripts/check_exec_check.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.exec_check import (  # noqa: E402
    EMPTY_MARKERS,
    PARAM_LABELS,
    _vocabulary,
    check_empty,
    check_reference,
    check_structure,
    inspect_execution,
)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}{'  ' + detail if detail else ''}")


ZERO = "查询结果：没有匹配的数据记录。"
HIT = "查询结果：共 81 条匹配记录，以下展示前 20 条：\n  1. [attendance] 张三（销售部）"

vocab = _vocabulary()
print(f"\n词表：{'已从库加载' if vocab else '读不到（结构校验整体跳过）'}")
if vocab:
    print(f"  data_type  {sorted(vocab['data_type'])}")
    print(f"  department {sorted(vocab['department'])}")

# —— 1. 结构校验：把猜测换成查表 ——
print("\n[1] 结构校验")
check("检查范围只含真正的受控参数",
      set(PARAM_LABELS) == {"data_type", "department"},
      f"实际={sorted(PARAM_LABELS)}")
check("employee_name 是开放集，不该进词表",
      "employee_name" not in PARAM_LABELS)
if vocab:
    bad = check_structure([{"name": "query_cleaned_records",
                            "args": {"data_type": "products"}}])
    check("data_type='products'（库里没有）→ 命中", len(bad) == 1, f"{bad}")
    bad = check_structure([{"name": "query_cleaned_records",
                            "args": {"data_type": "sales", "department": "销售部"}}])
    check("合法参数 → 通过", bad == [], f"{bad}")
    bad = check_structure([{"name": "query_cleaned_records",
                            "args": {"department": "市场部"}}])
    check("department='市场部'（库里没有）→ 命中", len(bad) == 1, f"{bad}")
    check("空参数不参与判定（= 没筛这一维）",
          check_structure([{"name": "get_summary_stats", "args": {}}]) == [])
    check("没有工具调用时通过", check_structure([]) == [])
else:
    check("读不到词表时整体放行（不能拦）", check_structure(
        [{"name": "x", "args": {"data_type": "products"}}]) == [])

# —— 2. 空集语义 ——
print("\n[2] 空集语义")
check("零行 + 「有哪些」→ 命中", check_empty("公司有哪些在售产品？", ZERO))
check("零行 + 「多少」→ 命中", check_empty("11月有多少条异常记录？", ZERO))
check("零行 + 没有非空预设 → 不命中", not check_empty("本月数据采集完了吗？", ZERO))
check("非零行 + 「有多少」→ 不命中", not check_empty("有多少条异常？", HIT))
check("三句空结果原文都被认出来",
      all(check_empty("有哪些", m) for m in EMPTY_MARKERS))

# —— 3. 回指校验（只标记） ——
print("\n[3] 回指校验")
check("问「为什么」+ 纯数据答案 → 存疑",
      check_reference("大促期间为什么只有销售部能申请加班额度？", HIT))
check("问「为什么」+ 答案有制度落点 → 不存疑",
      not check_reference("大促期间为什么只有销售部能申请加班额度？",
                          "因为《加班管理办法》第 3 条规定，大促保障额度只批给一线部门。"))
check("非因果问题 → 不存疑", not check_reference("销售部11月的异常率是多少？", HIT))
check("问「规则」+ 纯数据答案 → 存疑",
      check_reference("11月有多少条记录没通过格式校验规则？", HIT))

# —— 4. 三段合起来 ——
print("\n[4] inspect_execution")
v = inspect_execution("公司有哪些在售产品？",
                      [{"name": "query_cleaned_records",
                        "args": {"data_type": "products"}}], ZERO)
check("结构 + 空集都命中，但空集被结构挡住（两段互斥）",
      bool(v["structure"]) and v["empty"] is False and v["blocked"] is True,
      f"{v}")
v = inspect_execution("有哪些异常记录？",
                      [{"name": "get_anomaly_details", "args": {"data_type": "sales"}}], ZERO)
check("结构通过时，空集独立成立",
      v["structure"] == [] and v["empty"] is True and v["blocked"] is True)
v = inspect_execution("销售部11月的异常率是多少？",
                      [{"name": "get_summary_stats", "args": {"data_type": "attendance"}}],
                      HIT)
check("三段全过 → 不拦", v["blocked"] is False and v["reference"] is False, f"{v}")
v = inspect_execution("为什么只有销售部能申请？",
                      [{"name": "query_cleaned_records",
                        "args": {"data_type": "attendance"}}], HIT)
check("回指存疑但 blocked=False（只标记，不拦）",
      v["reference"] is True and v["blocked"] is False, f"{v}")

# —— 5. 结论要能喂飞轮 ——
print("\n[5] 结论的可用性")
v = inspect_execution("销售部11月的异常率是多少？",
                      [{"name": "get_summary_stats", "args": {"data_type": "attendance"}}],
                      HIT)
check("干净行也带 vocab 位（否则和「压根没跑」分不开）",
      v["vocab"] == (vocab is not None) and v["blocked"] is False,
      f"vocab={v['vocab']}")
check("四段结论齐全，可整条落 trace",
      set(v) == {"structure", "empty", "reference", "blocked", "vocab"},
      f"实际={sorted(v)}")
check("读不到词表时 vocab=False，但 blocked 不受影响",
      inspect_execution("公司有哪些在售产品？",
                        [{"name": "query_cleaned_records",
                          "args": {"data_type": "products"}}],
                        ZERO)["blocked"] == (vocab is not None))

# —— 汇总 ——
print(f"\n{'=' * 56}")
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print(f"  ❌ {f}")
    sys.exit(1)
print("全部通过 ✅")

"""把 8 个生成块合并成两套评测集，并做全量校验。

生成块见 evalset_spec.md：压力集 400（data120/doc120/hybrid80/edge80）
+ 回归集 400（data220/doc120/hybrid32/edge28）。各块由独立生成者产出，
**它们会串**——同一事实被两块各写一遍是主要风险，所以合并时必须查重。

校验项：
  1. 条数与分层构成是否对齐 evalset_spec.md 里的分层表
  2. 问题查重：块内 + 跨块（精确 + 归一化去虚词后）
  3. 字段自洽：expected_intent 与 category 一致；edge 的 expected_intent 必须是空串
  4. 工具名合法、doc/hybrid 必须带 expected_doc_keywords
  5. 实体合法性：问题里出现的部门/员工是否真实存在，员工与部门的搭配是否正确
     （第 5 项专门抓「王五属销售部」这类——真实映射里王五在运营部）

输出：eval_dataset_pressure.json / eval_dataset_regression.json

用法：
    python scripts/build_evalset.py          # 只校验，不写文件
    python scripts/build_evalset.py --write  # 校验通过后写文件
"""

import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRESSURE_BLOCKS = [
    ("evalset_pressure_data.json", "data_query", 120),
    ("evalset_pressure_doc.json", "doc_query", 120),
    ("evalset_pressure_hybrid.json", "hybrid", 80),
    ("evalset_pressure_edge.json", "edge_case", 80),
]
REGRESSION_BLOCKS = [
    ("evalset_regression_data_a.json", "data_query", 110),
    ("evalset_regression_data_b.json", "data_query", 110),
    ("evalset_regression_doc.json", "doc_query", 120),
    ("evalset_regression_hybrid_edge.json", "hybrid+edge", 60),
]

TOOLS = {"query_cleaned_records", "get_summary_stats", "get_anomaly_details"}

# 真实映射（2026-09-28 直连 PG 核过，别照抄 evalset_spec.md —— 那份是生成时的初稿，有错）
EMPLOYEE_DEPT = {
    "张三": "技术部", "赵六": "技术部", "郑十": "技术部",
    "李四": "销售部", "孙七": "销售部", "钱十一": "销售部",
    "周八": "客服部", "陈十二": "客服部",
    "王五": "运营部", "吴九": "运营部",
}
DEPARTMENTS = ["销售部", "技术部", "客服部", "运营部"]

# 归一化时剥掉的虚词，用于「换了说法但事实相同」的查重
STOP = re.compile(r"(是多少|有多少|多少|怎么样|是什么|有哪些|哪些|分别|一共|总共|"
                  r"的|了|吗|呢|啊|呀|请问|帮我|查一下|看一下|统计|情况|"
                  r"[\s，。？！,?!、]+)")


def norm(q: str) -> str:
    return STOP.sub("", q)


def load_blocks(blocks, tag):
    out, seen_files = [], []
    for fn, expect_cat, expect_n in blocks:
        path = os.path.join(ROOT, fn)
        if not os.path.exists(path):
            print(f"  ✗ 缺文件 {fn}")
            continue
        rows = json.load(open(path, encoding="utf-8"))
        seen_files.append((fn, len(rows), expect_n))
        for r in rows:
            r["_block"] = fn
            r["_set"] = tag
        out.extend(rows)
    return out, seen_files


def main():
    pressure, pf = load_blocks(PRESSURE_BLOCKS, "pressure")
    regression, rf = load_blocks(REGRESSION_BLOCKS, "regression")

    print("=" * 70)
    print("块装载")
    print("=" * 70)
    ok = True
    for tag, files, total, want in [
        ("压力集", pf, len(pressure), 400), ("回归集", rf, len(regression), 400)
    ]:
        print(f"\n{tag}：{total} 条（目标 {want}）{'✓' if total == want else '✗'}")
        for fn, got, exp in files:
            flag = "✓" if got == exp else f"✗ 期望{exp}"
            if got != exp:
                ok = False
            print(f"    {fn:<42}{got:>5}  {flag}")

    all_rows = pressure + regression

    # —— 1. 构成 ——
    print("\n" + "=" * 70)
    print("分层构成")
    print("=" * 70)
    for tag, rows in [("压力集", pressure), ("回归集", regression)]:
        c = collections.Counter(r.get("category") for r in rows)
        n = len(rows)
        print(f"\n{tag}：{n} 条")
        for k in ["data_query", "doc_query", "hybrid", "edge_case"]:
            print(f"    {k:<14}{c.get(k, 0):>5}  {c.get(k, 0) / n:>6.1%}")

    # —— 2. 查重 ——
    print("\n" + "=" * 70)
    print("查重")
    print("=" * 70)
    qs = [r["question"].strip() for r in all_rows]
    dup = [q for q, n in collections.Counter(qs).items() if n > 1]
    print(f"精确重复：{len(dup)} 组")
    for d in dup[:10]:
        print(f"    「{d}」")
    if dup:
        ok = False

    # 归一化激进的副作用：「？？？」和「帮我查一下」都会被剥成空串，假报重复。
    # 归一化结果短于 3 字的直接跳过——那个长度已经没有判别力了。
    normed = collections.defaultdict(list)
    for r in all_rows:
        k = norm(r["question"])
        if len(k) >= 3:
            normed[k].append(r)
    near = {k: v for k, v in normed.items() if len(v) > 1}
    print(f"归一化后重复：{len(near)} 组（已剔除归一化空串的假阳性）")
    for k, v in list(near.items())[:12]:
        blocks = {x["_block"] for x in v}
        same = "块内" if len(blocks) == 1 else "跨块"
        print(f"    [{same}] {[x['question'] for x in v]}")
    if near:
        ok = False

    # 跨集重复（压力 vs 回归）
    pset = {norm(r["question"]) for r in pressure}
    rset = {norm(r["question"]) for r in regression}
    cross = pset & rset
    print(f"压力集 ∩ 回归集：{len(cross)} 条")
    for c in list(cross)[:8]:
        print(f"    「{c}」")

    # —— 3. 字段自洽 ——
    print("\n" + "=" * 70)
    print("字段自洽")
    print("=" * 70)
    bad_intent = [r for r in all_rows
                  if r.get("category") == "edge_case" and r.get("expected_intent") != ""]
    print(f"edge_case 的 expected_intent 非空串：{len(bad_intent)} 条  "
          f"{'✓ 全部正确留空' if not bad_intent else '✗'}")
    for r in bad_intent[:5]:
        print(f"    id={r['id']} {r['question'][:40]} → {r.get('expected_intent')!r}")
    if bad_intent:
        ok = False

    mismatch = [r for r in all_rows
                if r.get("category") != "edge_case"
                and r.get("expected_intent") != r.get("category")]
    print(f"expected_intent 与 category 不一致：{len(mismatch)} 条")
    for r in mismatch[:5]:
        print(f"    id={r['id']} cat={r.get('category')} intent={r.get('expected_intent')}")
    if mismatch:
        ok = False

    bad_tool = [r for r in all_rows
                if any(t not in TOOLS for t in (r.get("expected_tools") or []))]
    print(f"非法工具名：{len(bad_tool)} 条")
    for r in bad_tool[:5]:
        print(f"    id={r['id']} {r.get('expected_tools')}")
    if bad_tool:
        ok = False

    miss_kw = [r for r in all_rows
               if r.get("expected_intent") in ("doc_query", "hybrid")
               and not r.get("expected_doc_keywords")]
    print(f"doc/hybrid 缺 expected_doc_keywords：{len(miss_kw)} 条")
    if miss_kw:
        ok = False

    # —— 4. 实体合法性 ——
    print("\n" + "=" * 70)
    print("实体合法性")
    print("=" * 70)
    bad_emp = []
    for r in all_rows:
        q = r["question"]
        mentions = [e for e in EMPLOYEE_DEPT if e in q]
        if len(mentions) >= 2:
            # 一条问题里同时点了两个不同部门的员工，通常是"对比"——合法
            continue
        for e in mentions:
            # 如果问题里还点了部门，且该部门不是这个员工的部门 → 可疑
            depts = [d for d in DEPARTMENTS if d in q]
            if depts and EMPLOYEE_DEPT[e] not in depts:
                bad_emp.append((r, e, depts))
    print(f"员工与部门搭配可疑：{len(bad_emp)} 条")
    for r, e, depts in bad_emp[:10]:
        print(f"    id={r['id']:<5} {e}（真实在{EMPLOYEE_DEPT[e]}）却与 {depts} 同现"
              f" | {r['question'][:46]}")

    # 抓「N 个汉字 + 部」会误伤「哪个部」「全部」「占全部」这类泛指——判据要改成
    # 只关心**编造出来的部门名**：找一个真实部门名都不含的「X部」token。
    # ⚠️ 只查 data_query。doc/hybrid 类**合法引用制度里的组织名**——
    # 「人力资源部」「人事部」是考勤制度和 KPI 手册里的"制定部门/适用部门"，
    # 它们不在 cleaned_records 里，但出现在 doc 类问题里完全正确。
    # 把 DEPARTMENTS（数据侧）当唯一合法部门名会大量误报。
    fake_dept = []
    for r in all_rows:
        if r.get("category") not in ("data_query",):
            continue
        for m in re.findall(r"[一-龥]{1,6}部(?!门)", r["question"]):
            if any(d in m for d in DEPARTMENTS):
                continue                      # 含真实部门名，合法
            if re.search(r"(哪个|哪些|四个|该|全部|各|其他|这|那|本|大)", m):
                continue                      # 泛指，不是部门名
            if re.search(r"[的了是在有和与或]", m):
                continue                      # 含虚词，是切出来的碎片（如"超过的部"）
            fake_dept.append((r, m))
    print(f"data 类里疑似编造的部门名：{len(fake_dept)} 条")
    for r, m in fake_dept[:10]:
        print(f"    id={r['id']:<5} 「{m}」 | {r['question'][:46]}")

    # —— 5. 工具分布 ——
    print("\n" + "=" * 70)
    print("工具分布")
    print("=" * 70)
    # 规格里的「工具选择：data 类三个工具各 40 条」说的是**条数**不是调用数，
    # 所以按每条的首个工具归类，只看 data 类。
    for tag, rows in [("压力集", pressure), ("回归集", regression)]:
        data_rows = [r for r in rows if r.get("category") == "data_query"]
        c = collections.Counter(
            (r.get("expected_tools") or ["(空)"])[0] for r in data_rows
        )
        print(f"\n{tag} data 类 {len(data_rows)} 条，按主工具：")
        for t, n in c.most_common():
            print(f"    {t:<28}{n:>5}")

    print("\n" + "=" * 70)
    print("结论：" + ("✓ 全部通过" if ok else "✗ 有未通过项，见上"))
    print("=" * 70)

    if "--write" in sys.argv and ok:
        for tag, rows, fn in [("pressure", pressure, "eval_dataset_pressure.json"),
                              ("regression", regression, "eval_dataset_regression.json")]:
            for r in rows:
                r.pop("_block", None)
                r.pop("_set", None)
            out = os.path.join(ROOT, fn)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            print(f"已写入 {fn}（{len(rows)} 条）")
    elif "--write" in sys.argv:
        print("校验未通过，不写文件。")


if __name__ == "__main__":
    main()

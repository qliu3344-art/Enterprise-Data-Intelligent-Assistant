"""意图路由冒烟测试 — 不需要 API Key，只验证纯函数。

重点覆盖薄适配层：OpenAI 和 DashScope 的返回结构不一样，必须都能归一化成
{token: logprob}，否则换厂商那天整个路由会静默失效。

用法：
    python scripts/check_intent_route.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.cleaner import _batched  # noqa: E402
from app.services.intent_router import (  # noqa: E402
    INTENT_CONFIDENCE_THRESHOLD,
    LABEL_TO_INTENT,
    RULE_CONFIDENCE_CAP,
    RULE_CONF_DOUBLE_HIT,
    RULE_CONF_NO_HIT,
    RULE_CONF_SINGLE_HIT,
    _derive,
    _normalize_top_logprobs,
    rule_classify,
)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}{'  ' + detail if detail else ''}")


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) < tol


# —— 1. 薄适配层：OpenAI 风格 ——
print("\n[1] 适配层 — OpenAI 风格（top_logprobs 是 [{token, logprob}]）")
openai_choice = {
    "logprobs": {
        "content": [
            {
                "token": "数",
                "logprob": math.log(0.8),
                "top_logprobs": [
                    {"token": "数", "logprob": math.log(0.8)},
                    {"token": "文", "logprob": math.log(0.15)},
                    {"token": "混", "logprob": math.log(0.05)},
                ],
            }
        ]
    }
}
chosen, _lp, top = _normalize_top_logprobs(openai_choice)
check("归一化出 3 个候选", len(top) == 3, f"top={list(top.keys())}")
check("选中 token = 数", chosen == "数")
d = _derive(top, chosen)
check("p_top ≈ 0.8", approx(d["p_top"], 0.8, 1e-4), f"p_top={d['p_top']:.4f}")
check("label_mass ≈ 1.0", approx(d["label_mass"], 1.0, 1e-4), f"label_mass={d['label_mass']:.4f}")
check("intent = data_query", d["intent"] == "data_query")

# —— 2. 薄适配层：DashScope 风格 ——
print("\n[2] 适配层 — DashScope 风格（token 直接当键，嵌套层级不同）")
dashscope_choice = {
    "logprobs": {
        "content": [{"数": math.log(0.55), "文": math.log(0.40), "混": math.log(0.05)}]
    }
}
chosen2, _lp2, top2 = _normalize_top_logprobs(dashscope_choice)
check("归一化出 3 个候选", len(top2) == 3, f"top={list(top2.keys())}")
check("选中 token = 数（概率最大）", chosen2 == "数")
d2 = _derive(top2, chosen2)
check("intent = data_query", d2["intent"] == "data_query")
check("margin = 0.55 - 0.40 = 0.15", approx(d2["margin"], 0.15, 1e-4), f"margin={d2['margin']:.4f}")

# —— 3. 绝不重新归一化 ——
print("\n[3] 关键一条：绝不重新归一化")
# 只有 0.5 的质量落在合法标签上，另外 0.5 漏给了垃圾 token。
# 若把它归一化成 1.0，label_mass 就等于 1，这个「prompt 约束失效」的信号就被抹掉了。
leaky = {"数": math.log(0.3), "文": math.log(0.2)}
d3 = _derive(leaky, "数")
check("label_mass = 0.5（没被归一化成 1.0）", approx(d3["label_mass"], 0.5, 1e-4),
      f"label_mass={d3['label_mass']:.4f}")
check("p_top = 0.3（没被抬到 0.6）", approx(d3["p_top"], 0.3, 1e-4), f"p_top={d3['p_top']:.4f}")

# —— 4. 模型吐了非法 token，但合法标签在候选里 ——
print("\n[4] 模型吐非法 token（prompt 约束在漏，但还能路由）")
junk_choice = {
    "logprobs": {
        "content": [
            {
                "token": "答",
                "logprob": math.log(0.5),
                "top_logprobs": [
                    {"token": "答", "logprob": math.log(0.5)},
                    {"token": "文", "logprob": math.log(0.35)},
                    {"token": "数", "logprob": math.log(0.1)},
                ],
            }
        ]
    }
}
c4, _lp4, top4 = _normalize_top_logprobs(junk_choice)
d4 = _derive(top4, c4)
check("按合法标签里概率最大的路由 → doc_query", d4["intent"] == "doc_query")
check("label_mass = 0.45", approx(d4["label_mass"], 0.45, 1e-4), f"label_mass={d4['label_mass']:.4f}")

# —— 5. 一个合法标签都没有 ——
print("\n[5] 前 5 个候选里全是非法 token")
d5 = _derive({"答": math.log(0.6), "案": math.log(0.4)}, "答")
check("_derive 返回 None（交给 L2 规则）", d5 is None)

# —— 6. 返回体里没有 logprobs ——
print("\n[6] A 类失败：返回体里根本没有 logprobs")
try:
    _normalize_top_logprobs({"message": {"content": "数"}})
    check("抛异常", False)
except ValueError as e:
    check("抛异常", True, str(e))

# —— 7. L2 规则层：匹配的是用户问题，不是模型输出 ——
print("\n[7] L2 规则降级（匹配 question）")
check("只命中 doc → doc_query",
      rule_classify("加班超过多少小时算异常？公司制度怎么规定的")[0] == "doc_query")
check("只命中 data → data_query",
      rule_classify("销售部11月的异常率是多少")[0] == "data_query")
check("两个都命中 → hybrid",
      rule_classify("销售部加班异常率这个指标，制度里怎么规定的")[0] == "hybrid")
check("都不命中 → data_query（猜错代价最小的分支）",
      rule_classify("郑十最近怎么样") == ("data_query", RULE_CONF_NO_HIT))
print("  · 单命中 confidence = %.2f" % rule_classify("制度怎么规定的")[1])
check("单命中 = 0.60", approx(rule_classify("制度怎么规定的")[1], RULE_CONF_SINGLE_HIT))
check("双命中 = 0.65", approx(rule_classify("制度规定的异常率标准")[1], RULE_CONF_DOUBLE_HIT))

# —— 8. 护栏：规则 confidence 必须全部低于阈值 ——
print("\n[8] 护栏：规则路径的 confidence 上界")
rule_confs = [RULE_CONF_DOUBLE_HIT, RULE_CONF_SINGLE_HIT, RULE_CONF_NO_HIT]
check("全部 < 阈值 %.2f（强制走安全网）" % INTENT_CONFIDENCE_THRESHOLD,
      all(c < INTENT_CONFIDENCE_THRESHOLD for c in rule_confs), str(rule_confs))
check("常量上界 = 0.70", approx(RULE_CONFIDENCE_CAP, 0.70))
check("上界本身也 < 阈值", RULE_CONFIDENCE_CAP < INTENT_CONFIDENCE_THRESHOLD)
check("阈值 = 0.75（C_h=1 / C_e=4 推出来的）", approx(INTENT_CONFIDENCE_THRESHOLD, 0.75))

# —— 9. 标签表 ——
print("\n[9] 标签表")
check("三个标签 → 三条引擎",
      LABEL_TO_INTENT == {"数": "data_query", "文": "doc_query", "混": "hybrid"})

# —— 10. 清洗候选分批 ——
print("\n[10] 清洗候选分批：覆盖百分之百")
cands = list(range(100))
batches = list(_batched(cands, 30))
check("100 条 / 批 30 → 4 批", len(batches) == 4, f"批次大小={[len(b) for b in batches]}")
check("合并回去一条不丢", [x for b in batches for x in b] == cands)
check("空列表不产生批次", list(_batched([], 30)) == [])

# —— 汇总 ——
print(f"\n{'=' * 56}")
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print(f"  ❌ {f}")
    sys.exit(1)
print("全部通过 ✅")

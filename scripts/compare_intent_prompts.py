"""意图路由 prompt 的 A/B 对比：换 prompt 前先量准确率和延迟。

为什么必须先量延迟：路由单次超时是 500ms（见 app/retry.py 的推导），实测地板约
300ms —— 大头在 prefill。**prompt 变长会直接推高这个地板**，一旦越过 500ms，
调用会被掐断、跌进 L2 规则兜底，准确率反而崩。所以改 prompt 不能拍脑袋，
得同时看两个数：分类对不对、以及有没有被超时打掉。

本脚本 monkeypatch 模块级 INTENT_PROMPT，不动源码。采样默认偏向 doc 类——
那是已知的弱项。

用法：
    python scripts/compare_intent_prompts.py [--n-doc 30] [--n-other 8]
"""

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.services.intent_router as router

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# —— 候选 prompt：在原文基础上补一段边界规则 ——
# 针对的失败模式：制度类问题只要沾了数据侧词汇（迟到次数/加班/订单/质量分），
# 就被判成 data 或 hybrid。实测 doc 类准确率只有 40%。
CANDIDATE = """判断下面这句话属于哪一类，只回答一个字，不要任何解释、不要标点。

数：查询数据库中的结构化数据（统计、数量、业绩、指标值、排名、异常率、质量分、某个部门或某个人的数据）
文：查询公司制度、规定、政策、标准、流程（怎么算、怎么判定、定义、规则、条件）
混：需要同时查数据库和文档才能完整回答（问「为什么」且同时涉及具体数据和制度依据）

注意：问「制度怎么规定」的，答案翻文档就有，是文——标准多少、门槛几、谁审批、什么流程、
什么后果、怎么算，都算。这类问题常带数字（多少小时算异常、几天内审批、几倍算极端），
但那是制度定的门槛，不是库里的实际值，不要因为看见数字就判成数或混。

用户问题：{question}

只回答一个字（数／文／混）："""


def sample():
    """采样：doc 全取弱项，data/hybrid 各取若干做回归检查。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-doc", type=int, default=30)
    ap.add_argument("--n-other", type=int, default=8)
    ap.add_argument("--dataset", default="eval_dataset_regression.json")
    args = ap.parse_args()

    rows = json.load(open(os.path.join(ROOT, args.dataset), encoding="utf-8"))
    out = []
    for cat, n in [("doc_query", args.n_doc), ("data_query", args.n_other),
                   ("hybrid", args.n_other)]:
        pool = [r for r in rows if r["category"] == cat and r.get("expected_intent")]
        out.extend(pool[:n])
    return out


def run(rows, prompt, label):
    saved = router.INTENT_PROMPT
    router.INTENT_PROMPT = prompt
    hits, lat, fallback = 0, [], 0
    per_cat = {}
    try:
        for r in rows:
            t0 = time.time()
            res = router.classify_intent(r["question"])
            dt = (time.time() - t0) * 1000
            lat.append(dt)
            # path 的合法值是 "logprobs" | "rule"（见 intent_router），
            # degrade 正常时是空串。走 rule 就是被超时/异常打掉了。
            if res.get("path") != "logprobs":
                fallback += 1
            ok = res["intent"] == r["expected_intent"]
            hits += ok
            c = per_cat.setdefault(r["expected_intent"], [0, 0])
            c[0] += ok
            c[1] += 1
    finally:
        router.INTENT_PROMPT = saved

    n = len(rows)
    lat_s = sorted(lat)
    print(f"\n【{label}】prompt {len(prompt)} 字符")
    print(f"  准确率 {hits}/{n} = {hits / n:.1%}   未走 LLM（超时/降级）{fallback}/{n}")
    for c, (h, t) in sorted(per_cat.items()):
        print(f"    {c:<12}{h}/{t} = {h / t:.1%}")
    print(f"  延迟 ms：中位 {statistics.median(lat_s):.0f}  "
          f"p90 {lat_s[int(n * 0.9)]:.0f}  max {lat_s[-1]:.0f}")
    return hits / n, fallback


def main():
    rows = sample()
    print(f"采样 {len(rows)} 条")
    base_acc, base_fb = run(rows, router.INTENT_PROMPT, "现行 prompt")
    new_acc, new_fb = run(rows, CANDIDATE, "候选 prompt")
    print(f"\n{'=' * 62}")
    print(f"准确率 {base_acc:.1%} → {new_acc:.1%}  ({new_acc - base_acc:+.1%})")
    print(f"被超时/降级打掉  {base_fb} → {new_fb} 条")
    if new_fb > base_fb + len(rows) * 0.05:
        print("⚠️ 候选 prompt 明显更容易被超时打掉——变长推高了 prefill，"
              "采纳前要重新评估 ROUTE_TIMEOUT_SECONDS")
    print("=" * 62)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""意图路由 prompt 的全量 A/B 验证（372 条回归集，不抽样）。

为什么不用 compare_intent_prompts.py：那个脚本只采 46 条、且每类取**头几条**，
正好覆盖不到要修的失败模式——误升集中在 id 37~42、206~218 那一段。
改 prompt 看的必须是全量，否则改好了也看不见。

同时量延迟：路由单次预算 500ms，prefill 是大头，prompt 变长会直接推高地板，
越过就跌进 L2 规则兜底、准确率反而崩（见 app/retry.py 的推导）。

用法（项目根目录）：
    python scripts/validate_intent_prompt.py
输出：现行 vs 候选的准确率、误升/漏答分解、延迟。
"""

import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.services.intent_router as router

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# —— 候选 prompt ——
# 针对两类误升（2026-10-07 实测 26 条）：
#   doc→hybrid 19 条：都是「制度怎么规定」，但带数据侧名词（大促/标签/阈值/基准），
#                     模型把**限定词**当成了数据侧需求 → 补注意四。
#   data→hybrid  7 条：「哪些记录被标记为异常」这类**纯列记录**，被注意二的
#                     「定义式判定」抓走了 → 注意二与注意三本就打架，这里给出一条
#                     可判的分界：要记录本身是数，要判定记录的标准是混。
CANDIDATE = """判断下面这句话属于哪一类，只回答一个字，不要任何解释、不要标点。

数：查询数据库中的结构化数据（统计、数量、业绩、指标值、排名、异常率、质量分、某个部门或某个人的数据）
文：查询公司制度、规定、政策、标准、流程（怎么算、怎么判定、定义、规则、条件）
混：回答时既要查数据库里的实际值，又要查文档才能确定判定标准、口径或分类依据

注意一：问「制度怎么规定」的，答案翻文档就有，是文——标准多少、门槛几、谁审批、什么流程、
什么后果、怎么算，都算。这类问题常带数字（多少小时算异常、几天内审批、几倍算极端），
但那是制度定的门槛，不是库里的实际值，不要因为看见数字就判成数或混。

注意二：要的不是记录本身，而是「按什么标准筛出来的」——问题里出现「不符合××」「构成××」
「需要××」这种定义式判定，而那个标准不是库里存好的字段——那是混，不是数。

注意三：「异常」「质量分」「排名」「指标」如果本身是库里存好的字段或标记，那么
「有多少条异常记录」「哪些记录被标记为异常」「哪类数据异常最多」要的是**记录本身**，
翻库就能答，是数。分界是：要记录本身是数，要判定记录的标准是混。

用户问题：{question}

只回答一个字（数／文／混）："""


def run(rows, prompt, label):
    saved = router.INTENT_PROMPT
    router.INTENT_PROMPT = prompt
    lat, fb = [], 0
    res_rows = []
    try:
        for i, r in enumerate(rows, 1):
            t0 = time.time()
            res = router.classify_intent(r["question"])
            lat.append((time.time() - t0) * 1000)
            if res.get("path") != "logprobs":
                fb += 1
            res_rows.append((r, res))
            if i % 50 == 0:
                print("   ...%d/%d" % (i, len(rows)))
    finally:
        router.INTENT_PROMPT = saved

    n = len(rows)
    hit = sum(1 for r, res in res_rows if res["intent"] == r["expected_intent"])
    over = sum(1 for r, res in res_rows
               if res["intent"] == "hybrid" and r["expected_intent"] != "hybrid")
    miss = sum(1 for r, res in res_rows
               if r["expected_intent"] == "hybrid" and res["intent"] != "hybrid")
    doc_as_data = sum(1 for r, res in res_rows
                      if r["expected_intent"] == "doc_query" and res["intent"] == "data_query")
    s = sorted(lat)
    print("\n【%s】prompt %d 字符" % (label, len(prompt)))
    print("  准确率 %d/%d = %.1f%%   降级 %d" % (hit, n, 100 * hit / n, fb))
    print("  误升到 hybrid %d   漏判 hybrid %d   doc→data %d" % (over, miss, doc_as_data))
    print("  延迟 ms：中位 %.0f  p90 %.0f  max %.0f" % (statistics.median(s), s[int(n * .9)], s[-1]))
    return dict(acc=hit / n, over=over, miss=miss, doc_as_data=doc_as_data, fb=fb,
                med=statistics.median(s), rows=res_rows)


def main():
    ds = [r for r in json.load(open(os.path.join(ROOT, "eval_dataset_regression.json"),
                                     encoding="utf-8"))
          if r.get("expected_intent")]
    print("全量 %d 条" % len(ds))
    base = run(ds, router.INTENT_PROMPT, "现行 prompt")
    cand = run(ds, CANDIDATE, "候选 prompt")

    print("\n" + "=" * 64)
    print("准确率      %.1f%% → %.1f%%  (%+.1fpt)" % (100 * base["acc"], 100 * cand["acc"],
                                                     100 * (cand["acc"] - base["acc"])))
    print("误升 hybrid  %3d → %3d" % (base["over"], cand["over"]))
    print("漏判 hybrid  %3d → %3d   ← 不能涨" % (base["miss"], cand["miss"]))
    print("doc→data     %3d → %3d" % (base["doc_as_data"], cand["doc_as_data"]))
    print("被降级       %3d → %3d" % (base["fb"], cand["fb"]))
    print("延迟中位    %.0f → %.0f ms  (预算 500)" % (base["med"], cand["med"]))
    print("=" * 64)

    # 逐条差分，看清改动了谁
    print("\n候选改判的条目（现行 → 候选）：")
    for (r, rb), (_, rc) in zip(base["rows"], cand["rows"]):
        if rb["intent"] != rc["intent"]:
            mark = "✅" if (rc["intent"] == r["expected_intent"]
                            and rb["intent"] != r["expected_intent"]) else (
                "❌" if rb["intent"] == r["expected_intent"] else "  ")
            print("  %s [标 %-10s] %-16s→%-16s %s"
                  % (mark, r["expected_intent"], rb["intent"], rc["intent"], r["question"][:34]))


if __name__ == "__main__":
    main()

"""复现「改造前」的自评置信度方案，在同一套评测集上跑出对照列。

背景：改造对照表是「自评 confidence vs logprobs」两列。改造后的代码还在，
**自评版已经被替换掉了**——只在 git 历史里（commit 7e9a354）。没有这一列，
对照表就只有一半，而且两列必须跑在同一套集子上才有可比性。

所以把那个版本原样搬到这里：同样的 prompt、同样的 JSON 解析、同样的关键词兜底。
**不要"顺手改进"它**——它要复现的是当时的真实行为，不是当时应该有的行为。

与现行版的差别（都是刻意的）：
  · 输出一整个 JSON（max_tokens=50），自报一个 0~1 的 confidence
  · temperature=0
  · 没有 500ms 路由超时预算，走 retry_call 的默认重试

用法：
    python scripts/eval_legacy_selfreport.py eval_dataset_pressure.json
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.messages import HumanMessage

from app.config import settings
from app.retry import retry_call

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INTENT_PROMPT = """判断以下用户问题的类型。只输出 JSON 格式，不要任何解释：
{{"intent": "data_query|doc_query|hybrid", "confidence": 0.0~1.0}}

标签定义：
- data_query：查询数据库中的结构化数据。关键词包括：数据、统计、数量、业绩、指标值、排名、多少条、异常率、质量分、某个部门的数据情况、具体人的数据。
- doc_query：查询公司制度、规定、政策、流程、标准等文档。关键词包括：规定、制度、政策、标准、流程、怎么算、怎么判定、定义、规则、条件。
- hybrid：需要同时查数据库和文档才能完整回答。特点是在问"为什么"同时涉及具体数据和制度依据。

用户问题：{question}

JSON："""


def _fallback_classify(raw_text: str) -> dict:
    """回退策略：精确词边界匹配（照抄 7e9a354）。"""
    import re

    text = raw_text.lower()
    has_hybrid = bool(re.search(r"\bhybrid\b", text))
    has_doc = bool(re.search(r"\bdoc_query\b|\bdoc\b", text))
    has_data = bool(re.search(r"\bdata_query\b|\bdata\b", text))

    if has_hybrid:
        return {"intent": "hybrid", "confidence": 0.6}
    elif has_doc:
        return {"intent": "doc_query", "confidence": 0.6}
    elif has_data:
        return {"intent": "data_query", "confidence": 0.6}
    return {"intent": "data_query", "confidence": 0.5}


def classify_intent(question: str) -> dict:
    """照抄 7e9a354 的 classify_intent。"""
    llm = ChatTongyi(
        model=settings.LLM_MODEL,
        dashscope_api_key=settings.DASHSCOPE_API_KEY,
        temperature=0,
        max_tokens=50,
    )
    prompt = INTENT_PROMPT.format(question=question)
    try:
        response = retry_call(llm.invoke, [HumanMessage(content=prompt)])
        raw = response.content.strip()
    except Exception as e:
        print(f"    ⚠️ 调用失败: {e}")
        return {"intent": "data_query", "confidence": 0.0}

    import json as _json
    try:
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = _json.loads(raw)
    except (_json.JSONDecodeError, ValueError):
        return _fallback_classify(raw)

    intent = result.get("intent", "").strip().lower()
    if intent not in ("data_query", "doc_query", "hybrid"):
        intent = _fallback_classify(raw)["intent"]

    confidence = float(result.get("confidence", 0.7))
    confidence = max(0.0, min(1.0, confidence))
    return {"intent": intent, "confidence": round(confidence, 2)}


def main():
    ds = sys.argv[1] if len(sys.argv) > 1 else "eval_dataset_pressure.json"
    rows = json.load(open(os.path.join(ROOT, ds), encoding="utf-8"))
    print(f"{ds}：{len(rows)} 条")

    out = []
    t0 = time.time()
    for i, item in enumerate(rows, 1):
        expected = item.get("expected_intent", "")
        if not expected:          # edge 类不参与意图评分（与 eval.py 同口径）
            continue
        res = classify_intent(item["question"])
        out.append({
            "question": item["question"],
            "expected": expected,
            "predicted": res["intent"],
            "confidence": res["confidence"],
            "correct": res["intent"] == expected,
        })
        if i % 40 == 0:
            print(f"  {i}/{len(rows)}  {sum(1 for r in out if r['correct'])}/{len(out)}")

    n = len(out)
    ok = sum(1 for r in out if r["correct"])
    report = {
        "meta": {"dataset": ds, "dataset_size": len(rows), "legacy_selfreport": True,
                 "eval_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "elapsed_seconds": round(time.time() - t0, 1)},
        "intent": out,
    }
    path = os.path.join(ROOT, f"eval_report_legacy_{os.path.basename(ds)}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n准确率 {ok}/{n} = {ok / n:.1%}")
    print(f"已保存 {path}")


if __name__ == "__main__":
    main()

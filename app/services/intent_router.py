"""意图路由器：用 LLM 轻量分类用户问题类型。

判断策略（用极简 prompt + temperature=0）：
  - data_query：查询数据库中的结构化数据（考勤/销售/客户/运营）
  - doc_query：查询公司制度、规定、手册等非结构化文档
  - hybrid：需要同时查数据库和查文档才能完整回答
"""

from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.messages import HumanMessage

from app.config import settings
from app.logger import logger
from app.retry import retry_call

INTENT_PROMPT = """判断以下用户问题的类型。只输出 JSON 格式，不要任何解释：
{{"intent": "data_query|doc_query|hybrid", "confidence": 0.0~1.0}}

标签定义：
- data_query：查询数据库中的结构化数据。关键词包括：数据、统计、数量、业绩、指标值、排名、多少条、异常率、质量分、某个部门的数据情况、具体人的数据。
- doc_query：查询公司制度、规定、政策、流程、标准等文档。关键词包括：规定、制度、政策、标准、流程、怎么算、怎么判定、定义、规则、条件。
- hybrid：需要同时查数据库和文档才能完整回答。特点是在问"为什么"同时涉及具体数据和制度依据。

用户问题：{question}

JSON："""


def classify_intent(question: str) -> dict:
    """分类用户问题的意图。

    Returns:
        {"intent": "data_query"|"doc_query"|"hybrid", "confidence": float}
    """
    logger.info(f"意图分类: {question[:80]}")

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
        logger.error(f"意图分类失败: {e}")
        return {"intent": "data_query", "confidence": 0.0}

    # 解析 LLM 返回的 JSON
    import json as _json
    try:
        # 清理可能的前后缀
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = _json.loads(raw)
    except (_json.JSONDecodeError, ValueError):
        # JSON 解析失败，回退到精确关键词匹配
        logger.warning(f"意图分类 JSON 解析失败，回退关键词匹配: {raw[:80]}")
        return _fallback_classify(raw)

    # 验证并标准化 intent 值
    intent = result.get("intent", "").strip().lower()
    if intent not in ("data_query", "doc_query", "hybrid"):
        intent = _fallback_classify(raw)["intent"]

    confidence = float(result.get("confidence", 0.7))
    confidence = max(0.0, min(1.0, confidence))

    logger.info(f"意图分类结果: {question[:40]}... → {intent} (confidence={confidence})")
    return {"intent": intent, "confidence": round(confidence, 2)}


def _fallback_classify(raw_text: str) -> dict:
    """回退策略：用精确词边界匹配判断意图（不用子串包含）。"""
    import re

    text = raw_text.lower()

    # 精确词边界匹配，避免 "not a hybrid" 被误判
    has_hybrid = bool(re.search(r'\bhybrid\b', text))
    has_doc = bool(re.search(r'\bdoc_query\b|\bdoc\b', text))
    has_data = bool(re.search(r'\bdata_query\b|\bdata\b', text))

    if has_hybrid:
        return {"intent": "hybrid", "confidence": 0.6}
    elif has_doc:
        return {"intent": "doc_query", "confidence": 0.6}
    elif has_data:
        return {"intent": "data_query", "confidence": 0.6}
    else:
        return {"intent": "data_query", "confidence": 0.3}

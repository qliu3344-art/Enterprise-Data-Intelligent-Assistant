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

INTENT_PROMPT = """判断以下用户问题的类型，只回复一个标签（不要解释）：

标签定义：
- data_query：查询数据库中的结构化数据。关键词包括：数据、统计、数量、业绩、指标值、排名、多少条、异常率、质量分、某个部门的数据情况、具体人的数据。
- doc_query：查询公司制度、规定、政策、流程、标准等文档。关键词包括：规定、制度、政策、标准、流程、怎么算、怎么判定、定义、规则、条件。
- hybrid：需要同时查数据库和文档才能完整回答。特点是在问"为什么"同时涉及具体数据和制度依据。

用户问题：{question}

标签："""


def classify_intent(question: str) -> dict:
    """分类用户问题的意图。

    Returns:
        {"intent": "data_query"|"doc_query"|"hybrid"}
    """
    logger.info(f"意图分类: {question[:80]}")

    llm = ChatTongyi(
        model=settings.LLM_MODEL,
        dashscope_api_key=settings.DASHSCOPE_API_KEY,
        temperature=0,
        max_tokens=10,
    )

    prompt = INTENT_PROMPT.format(question=question)

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip().lower()
    except Exception as e:
        logger.error(f"意图分类失败: {e}")
        return {"intent": "data_query"}  # 默认走 Agent

    # 解析标签
    if "hybrid" in raw:
        intent = "hybrid"
    elif "doc_query" in raw or "doc" in raw:
        intent = "doc_query"
    else:
        intent = "data_query"  # 默认

    logger.info(f"意图分类结果: {question[:40]}... → {intent}")
    return {"intent": intent}

"""Skill 注册表：intent → Skill 的映射。

新增一种能力时，只需在此注册一个新的 Skill，路由层（query.py）无需改动。
意图路由（intent_router）输出的 intent 值直接作为 skill 的 name 查表。
"""

from app.services.skills.base import Skill
from app.services.skills.data_query import data_query_handler
from app.services.skills.doc_query import doc_query_handler
from app.services.skills.hybrid import hybrid_handler
from app.services.skills.instructions import DATA_QUERY_INSTRUCTIONS

SKILLS: dict[str, Skill] = {
    "data_query": Skill(
        name="data_query",
        description="查询数据库中的结构化数据（考勤/销售/客户/运营）",
        instructions=DATA_QUERY_INSTRUCTIONS,
        handler=data_query_handler,
    ),
    "doc_query": Skill(
        name="doc_query",
        description="查询公司制度、规定、标准等非结构化文档",
        instructions="",  # RAG 是固定管线，不经 Agent 决策，无需注入操作手册
        handler=doc_query_handler,
    ),
    "hybrid": Skill(
        name="hybrid",
        description="需要同时查数据库和文档才能完整回答",
        instructions=DATA_QUERY_INSTRUCTIONS,  # 数据部分复用同一份操作手册
        handler=hybrid_handler,
    ),
}


def get_skill(intent: str) -> Skill:
    """按 intent 取 Skill，未知意图回退到 data_query（安全网）。"""
    return SKILLS.get(intent, SKILLS["data_query"])

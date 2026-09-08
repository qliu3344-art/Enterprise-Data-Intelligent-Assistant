"""doc_query Skill：查询公司制度、规定、标准等非结构化文档（RAG）。"""

import asyncio

from app.services.skills.base import SkillResult


async def doc_query_handler(question: str, thread_id: str = "default") -> SkillResult:
    """执行文档查询：走 RAG 管线（固定管线，不经 Agent 决策，无操作手册注入）。"""
    from app.services.rag.service import ask_rag

    result = await asyncio.to_thread(ask_rag, question)
    return SkillResult(
        answer=result.get("answer", ""),
        mode="rag",
        extra={
            "sources": result.get("sources", []),
            "chunks_count": result.get("chunks_count", 0),
        },
    )

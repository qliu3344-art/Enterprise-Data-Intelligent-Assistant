"""hybrid Skill：数据库 + 文档融合回答。

需要同时查结构化数据（Agent）和制度文档（RAG）才能完整回答的场景。
"""

import asyncio

from app.logger import logger
from app.services.skills.base import SkillResult
from app.services.skills.data_query import _collect_agent

FUSION_PROMPT = """你是一个专业的企业数据分析师。请综合以下两部分信息，回答用户问题。

## 数据库查询结果（结构化数据）
{agent_answer}

## 相关制度文档条款（公司政策和规定）
{rag_chunks}

## 回答要求
1. 先给出数据事实（具体数字），再结合制度条款解释原因
2. 用中文自然语言表达，清晰流畅，4-10 句话
3. 数据部分要精确引用数字，制度部分引用具体条款编号（如"第X条第X款"）
4. 如果数据结果与制度规定有关联（阈值/规则等），明确指出
5. 如果某部分信息缺失，诚实说明

## 用户问题
{question}

请回答："""


def _fuse_hybrid_answer(
    question: str, agent_answer: str, rag_answer: str, rag_chunks: list[dict]
) -> str:
    """用 LLM 将 Agent 数据结果和 RAG 制度条款融合为统一自然语言回答。

    关键策略：不依赖 RAG 的 LLM 回答（可能会说"未找到关于这个人的信息"），
    而是直接用检索到的原始制度 chunks 作为融合上下文。
    """
    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_core.messages import HumanMessage

    from app.config import settings

    if rag_chunks:
        chunks_text_parts = []
        for i, chunk in enumerate(rag_chunks[:5], 1):
            header = f"[条款{i}] 《{chunk.get('doc_title', '')}》"
            if chunk.get("chapter"):
                header += f" — {chunk['chapter']}"
            chunks_text_parts.append(f"{header}\n{chunk.get('text', '')}")
        chunks_text = "\n\n".join(chunks_text_parts)
    else:
        chunks_text = "（未检索到相关制度条款）"

    # 如果没有检索到制度条款，直接返回 Agent 答案
    if not rag_chunks:
        return agent_answer

    prompt = FUSION_PROMPT.format(
        agent_answer=agent_answer[:2000],
        rag_chunks=chunks_text[:2500],
        question=question,
    )

    try:
        llm = ChatTongyi(
            model=settings.LLM_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.1,
            max_tokens=1200,
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content
    except Exception as e:
        logger.error(f"融合回答生成失败: {e}")
        # 降级：数据结果 + RAG 回答拼合
        if rag_answer and "未找到" not in rag_answer:
            return f"{agent_answer}\n\n📋 制度依据：\n{rag_answer}"
        return agent_answer


async def hybrid_handler(question: str, thread_id: str = "default") -> SkillResult:
    """并行调用 Agent + RAG，再用 LLM 融合为统一答案。"""
    from app.services.rag.service import ask_rag

    # 并行：Agent 流式（数据）+ RAG（文档）
    agent_task = asyncio.create_task(_collect_agent(question, thread_id, "hybrid"))
    rag_result = await asyncio.to_thread(ask_rag, question)

    agent_answer, iterations, tools_used = await agent_task
    rag_answer = rag_result.get("answer", "")
    rag_sources = rag_result.get("sources", [])

    final_answer = await asyncio.to_thread(
        _fuse_hybrid_answer, question, agent_answer, rag_answer, rag_sources
    )

    return SkillResult(
        answer=final_answer,
        mode="hybrid",
        iterations=iterations,
        tools_used=tools_used,
        extra={
            "rag_data": {
                "chunks_count": rag_result.get("chunks_count", 0),
                "sources": rag_sources,
            },
        },
    )

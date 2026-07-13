"""RAG 核心服务：管线编排 + LLM 答案生成 + 引用溯源。

流程：
  1. 混合检索 → top-k chunks
  2. 构建 Prompt（含原文引用 + 格式要求）
  3. ChatTongyi 生成答案
  4. 返回 {answer, sources}
"""

from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.messages import HumanMessage

from app.config import settings
from app.logger import logger
from app.services.rag.retriever import retrieve as hybrid_retrieve

# —— RAG 生成 Prompt ——
RAG_PROMPT = """你是一个专业的企业制度咨询助手。请根据以下制度文档原文回答用户问题。

## 制度文档原文
{context}

## 回答要求
1. 用中文自然语言回答，简洁清晰，3-8 句话
2. **必须引用具体的条款编号**（如"根据《考勤管理制度》第八条第2款"）
3. 如果文档中没有相关信息，诚实说"文档中未找到相关规定"，**不要编造**
4. 如果多条文档都相关，综合引用，不要遗漏

## 用户问题
{question}

请回答："""


def _build_context(chunks: list[dict]) -> str:
    """将检索到的 chunks 组装为上下文。"""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        meta = chunk.get("metadata", {})
        doc_title = meta.get("doc_title", "未知文档")
        chapter = meta.get("chapter", "")
        header = f"[来源{i}] 《{doc_title}》"
        if chapter:
            header += f" — {chapter}"
        parts.append(f"{header}\n{chunk['text']}")
    return "\n\n---\n\n".join(parts)


def _extract_sources(chunks: list[dict]) -> list[dict]:
    """从检索结果中提取引用来源（含原始文本用于融合）。"""
    sources = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        sources.append({
            "doc_title": meta.get("doc_title", "未知文档"),
            "file_name": meta.get("file_name", ""),
            "chapter": meta.get("chapter", ""),
            "section": meta.get("section", ""),
            "score": chunk.get("score", 0),
            "text": chunk.get("text", ""),
        })
    return sources


def ask_rag(question: str, top_k: int = 5) -> dict:
    """RAG 问答主入口。

    Args:
        question: 用户自然语言问题
        top_k: 检索返回的 chunk 数

    Returns:
        {"answer": str, "sources": [...], "chunks_count": int}
    """
    logger.info(f"RAG 查询: {question[:100]}")

    # 1. 检索
    chunks = hybrid_retrieve(question, top_k=top_k)
    if not chunks:
        return {
            "answer": "未找到相关制度文档，请确认问题涉及的主题（考勤、销售、客户、运营、加班、KPI等）。",
            "sources": [],
            "chunks_count": 0,
        }

    # 2. 构建上下文
    context = _build_context(chunks)

    # 3. LLM 生成
    llm = ChatTongyi(
        model=settings.LLM_MODEL,
        dashscope_api_key=settings.DASHSCOPE_API_KEY,
        temperature=0.1,
        max_tokens=800,
    )

    prompt = RAG_PROMPT.format(context=context, question=question)
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        answer = response.content
    except Exception as e:
        logger.error(f"RAG LLM 生成失败: {e}")
        answer = "制度文档检索完成，但答案生成暂时失败，请稍后重试。"

    # 4. 提取来源
    sources = _extract_sources(chunks)

    return {
        "answer": answer,
        "sources": sources,
        "chunks_count": len(chunks),
    }

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
from app.services.rag.vector_store import fetch_parent_chunks

# 父块回填的长度上限。一条一块之后，一章通常 2~5 条；超过上限的大章
# 只带章前言 + 命中条款前后各一条，避免一个章把 top-k 的上下文挤满。
MAX_PARENT_CONTEXT_CHARS = 2000

# —— RAG 生成 Prompt ——
RAG_PROMPT = """你是一个专业的企业制度咨询助手。请根据以下制度文档原文回答用户问题。

## 制度文档原文
{context}

## 回答要求
1. 用中文自然语言回答，简洁清晰，3-8 句话
2. **必须引用到具体条款**（如"根据《考勤管理制度》第二章第六条"）；标为【本章上下文】的条款如果被用到，也要按条款引用
3. 如果文档中没有相关信息，诚实说"文档中未找到相关规定"，**不要编造**
4. 如果多条文档都相关，综合引用，不要遗漏

## 用户问题
{question}

请回答："""


def _header(meta: dict) -> str:
    header = f"《{meta.get('doc_title', '未知文档')}》"
    if meta.get("version"):
        header += f" {meta['version']}"
    if meta.get("chapter"):
        header += f" — {meta['chapter']}"
    return header


def _source_label(nos: list[int]) -> str:
    return f"[来源{'、'.join(str(n) for n in nos)}]"


def _strip_chapter_prefix(text: str, chapter: str) -> str:
    """去掉子块正文开头重复的 ## 章标题：整章已经有一个章头了。"""
    prefix = f"## {chapter}\n\n"
    return text[len(prefix):] if chapter and text.startswith(prefix) else text


def _neighbor_context(siblings: list[dict], hit_keys: set[str]) -> list[dict]:
    """大章兜底：章前言 + 每个命中条款的前后各一条。"""
    keep_idx: set[int] = set()
    for i, s in enumerate(siblings):
        if s["metadata"].get("chunk_key") in hit_keys:
            keep_idx.update(j for j in (i - 1, i + 1) if 0 <= j < len(siblings))
    return [
        s for i, s in enumerate(siblings)
        if s["metadata"].get("chunk_key") not in hit_keys
        and (not s["metadata"].get("section") or i in keep_idx)
    ]


def _build_context(chunks: list[dict]) -> str:
    """将检索结果组装为上下文：命中条款 + 同章父块。

    检索仍然以条款为单位（精度），生成时按 parent_key 回填整章（完整性）；
    父块不单独建向量、不参与排序，只在组装 Prompt 时取回。
    """
    source_no = {id(c): i for i, c in enumerate(chunks, 1)}

    groups: dict[tuple[str, str], list[dict]] = {}
    loose: list[dict] = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        if not meta.get("parent_key"):
            loose.append(chunk)
            continue
        groups.setdefault(
            (meta.get("file_name", ""), meta.get("parent_key", "")), []
        ).append(chunk)

    parents = fetch_parent_chunks(set(groups))

    parts: list[str] = []
    for (file_name, parent_key), hits in groups.items():
        meta = hits[0].get("metadata", {})
        chapter = meta.get("chapter", "")
        source_label = _source_label([source_no[id(c)] for c in hits])

        hit_keys = {c.get("metadata", {}).get("chunk_key", "") for c in hits}
        hit_text = "\n\n".join(
            _strip_chapter_prefix(c.get("text", ""), chapter) for c in hits
        )

        siblings = parents.get((file_name, parent_key), [])
        context_items = [
            s for s in siblings
            if s["metadata"].get("chunk_key") not in hit_keys
        ]
        context_text = "\n\n".join(
            _strip_chapter_prefix(s["text"], chapter) for s in context_items
        )
        if len(hit_text) + len(context_text) > MAX_PARENT_CONTEXT_CHARS:
            context_items = _neighbor_context(siblings, hit_keys)
            context_text = "\n\n".join(
                _strip_chapter_prefix(s["text"], chapter) for s in context_items
            )

        block = f"{source_label} {_header(meta)}\n【直接命中】\n{hit_text}"
        if context_items:
            block += f"\n\n【本章上下文】\n{context_text}"
        parts.append(block)

    for chunk in loose:
        meta = chunk.get("metadata", {})
        parts.append(
            f"{_source_label([source_no[id(chunk)]])} {_header(meta)}\n"
            f"{chunk.get('text', '')}"
        )

    return "\n\n---\n\n".join(parts)


def _extract_sources(chunks: list[dict]) -> list[dict]:
    """从检索结果中提取引用来源（含原始文本用于融合）。"""
    sources = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        sources.append({
            "doc_title": meta.get("doc_title", "未知文档"),
            "file_name": meta.get("file_name", ""),
            "doc_id": meta.get("doc_id", ""),
            "version": meta.get("version", ""),
            "chapter": meta.get("chapter", ""),
            "section": meta.get("section", ""),
            "clause_no": meta.get("clause_no", ""),
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
        model_kwargs={"request_timeout": settings.LLM_REQUEST_TIMEOUT_SECONDS},
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

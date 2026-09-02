"""长期记忆存储：用户画像与跨会话偏好的向量化存储。

复用 RAG 已搭建的 BGE Embedding + ChromaDB 基础设施，但使用独立的
collection（user_memory），与制度文档（enterprise_policies）隔离。

三级上下文管理中的「长期」层：
  - 写入：会话结束 / 用户反馈时调用 save_memory 沉淀记忆
  - 召回：查询时调用 recall_memory 按语义召回与当前话题最相关的记忆

核心原则：按需注入，不是全量注入 —— 只召回最相关的记忆拼进上下文，
不无脑塞满。
"""

import uuid

from langchain_core.messages import SystemMessage

from app.logger import logger
from app.services.rag.embedder import embed_documents, embed_query
from app.services.rag.vector_store import _get_client

# 独立 collection，与制度文档隔离
MEMORY_COLLECTION = "user_memory"

_collection = None


def _get_memory_collection():
    """获取或创建长期记忆 collection（懒加载单例）。"""
    global _collection
    if _collection is None:
        client = _get_client()
        _collection = client.get_or_create_collection(
            name=MEMORY_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def save_memory(session_id: str, content: str, metadata: dict | None = None) -> bool:
    """保存一条长期记忆。

    Args:
        session_id: 会话标识
        content: 记忆文本（用户画像 / 稳定偏好 / 重要结论）
        metadata: 额外元数据

    Returns:
        是否保存成功
    """
    content = (content or "").strip()
    if not content:
        return False

    try:
        collection = _get_memory_collection()
        embedding = embed_documents([content])[0]
        memory_id = f"mem_{uuid.uuid4().hex}"
        meta = {"session_id": session_id}
        if metadata:
            meta.update(metadata)
        collection.add(
            ids=[memory_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[meta],
        )
        logger.info(f"长期记忆已保存: {content[:50]}...")
        return True
    except Exception as e:
        logger.error(f"保存长期记忆失败: {e}")
        return False


def recall_memory(query: str, top_k: int = 3) -> list[dict]:
    """按语义召回与当前问题最相关的长期记忆。

    Returns:
        [{"text": str, "metadata": dict, "score": float}, ...]
    """
    try:
        collection = _get_memory_collection()
        if collection.count() == 0:
            return []

        embedding = embed_query(query)
        results = collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        hits = []
        if results.get("ids") and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                distance = results["distances"][0][i] if results["distances"] else 0
                # Cosine distance ∈ [0,2]，转换为相似度 ∈ [0,1]
                similarity = 1.0 - (distance / 2.0) if distance else 1.0
                hits.append({
                    "text": results["documents"][0][i] if results["documents"] else "",
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "score": round(similarity, 4),
                })
        return hits
    except Exception as e:
        logger.error(f"召回长期记忆失败: {e}")
        return []


def build_memory_message(question: str, top_k: int = 3) -> SystemMessage | None:
    """召回相关长期记忆，组装成 SystemMessage（无相关记忆时返回 None）。"""
    memories = recall_memory(question, top_k=top_k)
    if not memories:
        return None

    lines = [f"- {m['text']}" for m in memories]
    content = (
        "以下是该用户的历史偏好与长期背景，回答时可参考（不要主动提及记忆本身）：\n"
        + "\n".join(lines)
    )
    return SystemMessage(content=content)

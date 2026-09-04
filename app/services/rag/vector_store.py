"""向量存储：ChromaDB 持久化 + 自动索引管理。

启动时检查文档是否变更（通过 checksum），自动决定是否重建索引。
支持按 source（文档名）维度删除和更新。
"""

import hashlib
import json
import os
from pathlib import Path
from typing import List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.logger import logger
from app.services.rag.chunker import Chunk, chunk_all
from app.services.rag.document_loader import load_all_documents
from app.services.rag.embedder import embed_documents, embed_query

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CHROMA_PATH = PROJECT_ROOT / "data" / "chroma_db"
COLLECTION_NAME = "enterprise_policies"

_client: Optional[chromadb.PersistentClient] = None
_collection: Optional[object] = None


def _get_client() -> chromadb.PersistentClient:
    """获取 ChromaDB 持久化客户端（单例）。"""
    global _client
    if _client is None:
        os.makedirs(str(CHROMA_PATH), exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(CHROMA_PATH),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info(f"ChromaDB 已连接: {CHROMA_PATH}")
    return _client


def get_collection():
    """获取或创建 collection（不删除已有数据）。"""
    global _collection
    if _collection is None:
        client = _get_client()
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _compute_doc_checksum(content: str) -> str:
    """单份文档内容的 md5 指纹。"""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _compute_docs_checksums(documents) -> dict:
    """计算所有文档的 {file_name: md5} 映射。"""
    return {doc.file_name: _compute_doc_checksum(doc.content) for doc in documents}


def _checksum_path() -> Path:
    """获取 checksum 缓存文件路径。"""
    return CHROMA_PATH / ".docs_checksum"


def _load_stored_checksums() -> dict:
    """加载已持久化的 per-document checksum 映射。

    兼容旧版本：旧版存的是全局 md5 字符串，非 dict 一律视为空，
    触发一次全量重建以迁移到新格式。
    """
    path = _checksum_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_checksums(checksums: dict) -> None:
    """持久化 per-document checksum 映射。"""
    os.makedirs(str(CHROMA_PATH), exist_ok=True)
    _checksum_path().write_text(
        json.dumps(checksums, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def needs_reindex() -> bool:
    """检查是否有文档新增、删除或内容变更（per-document 比对）。"""
    documents = load_all_documents()
    if not documents:
        return False
    current = _compute_docs_checksums(documents)
    stored = _load_stored_checksums()
    return current != stored


def _chunk_id(file_name: str, chunk_index: int) -> str:
    """生成稳定的 chunk 唯一 ID（file_name + 文档内序号），增量写入不撞车。"""
    return f"{file_name}#{chunk_index}"


def _chunk_metadata(c: Chunk) -> dict:
    """构建 chunk 的 ChromaDB metadata。"""
    return {
        "doc_title": c.doc_title,
        "file_name": c.file_name,
        "chapter": c.chapter[:200] if c.chapter else "",
        "section": c.section[:200] if c.section else "",
    }


def _clear_collection(collection) -> None:
    """清空 collection 中所有向量（保留 collection，避免窗口期查询报错）。"""
    existing = collection.get()
    if existing and existing.get("ids"):
        collection.delete(ids=existing["ids"])
        logger.info(f"已清空 {len(existing['ids'])} 条旧向量")


def _index_documents(collection, documents) -> int:
    """分块 + 向量化 + 写入，返回写入的 chunk 数。"""
    chunks = chunk_all(documents)
    texts = [c.text for c in chunks]
    embeddings = embed_documents(texts)

    batch_size = 50
    for i in range(0, len(chunks), batch_size):
        end = min(i + batch_size, len(chunks))
        batch = chunks[i:end]
        collection.add(
            ids=[_chunk_id(c.file_name, c.chunk_index) for c in batch],
            embeddings=embeddings[i:end],
            documents=[c.text for c in batch],
            metadatas=[_chunk_metadata(c) for c in batch],
        )
    return len(chunks)


def index_all(force: bool = False) -> dict:
    """加载文档 → 分块 → 向量化 → 写入 ChromaDB。

    force=False：per-document 增量——只重算新增/变更的文档、删除已移除的文档，
                 未变更的零成本跳过。
    force=True：全量重建（清空后重算所有文档）。

    Returns:
        {"documents", "chunks", "reindexed", "added", "updated", "removed"}
    """
    documents = load_all_documents()
    if not documents:
        return {"documents": 0, "chunks": 0, "reindexed": False,
                "added": 0, "updated": 0, "removed": 0}

    current = _compute_docs_checksums(documents)
    stored = _load_stored_checksums()
    collection = get_collection()

    # —— 全量重建：force=True，或首次迁移（旧全局 md5 格式 → 无法 diff）——
    if force or (not stored and collection.count() > 0):
        _clear_collection(collection)
        count = _index_documents(collection, documents)
        _save_checksums(current)
        logger.info(f"全量索引完成: {len(documents)} 份文档 → {count} chunks")
        return {"documents": len(documents), "chunks": count, "reindexed": True,
                "added": len(documents), "updated": 0, "removed": 0}

    # —— 无变更，跳过 ——
    if current == stored:
        count = collection.count()
        logger.info(f"文档未变更，跳过索引（已有 {count} 个向量）")
        return {"documents": len(documents), "chunks": count, "reindexed": False,
                "added": 0, "updated": 0, "removed": 0}

    # —— per-document 增量 ——
    added = [name for name in current if name not in stored]
    removed = [name for name in stored if name not in current]
    updated = [name for name in current if name in stored and current[name] != stored[name]]

    # 删除已移除文档的旧向量
    for name in removed:
        collection.delete(where={"file_name": name})
        logger.info(f"已删除移除文档的向量: {name}")

    # 变更文档：先删旧向量，再重新写入
    for name in updated:
        collection.delete(where={"file_name": name})
        logger.info(f"已删除变更文档的旧向量: {name}")

    to_reindex = added + updated
    changed_docs = [d for d in documents if d.file_name in to_reindex]
    _index_documents(collection, changed_docs)

    _save_checksums(current)

    logger.info(
        f"增量索引完成: 新增 {len(added)} / 更新 {len(updated)} / 删除 {len(removed)}，"
        f"当前 {collection.count()} 个向量"
    )
    return {
        "documents": len(documents),
        "chunks": collection.count(),
        "reindexed": True,
        "added": len(added),
        "updated": len(updated),
        "removed": len(removed),
    }


def search(query_embedding: List[float], top_k: int = 20) -> list[dict]:
    """向量相似度检索。

    Returns:
        [{"text": str, "metadata": dict, "score": float}, ...]
    """
    collection = get_collection()
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    if results["ids"] and results["ids"][0]:
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


def get_document_stats() -> list[dict]:
    """获取已索引文档的统计信息。"""
    collection = get_collection()
    if collection.count() == 0:
        return []

    # 从 metadata 中聚合统计
    result = collection.get(include=["metadatas"])
    doc_map: dict[str, int] = {}
    for meta in result["metadatas"]:
        doc = meta.get("doc_title", "未知")
        doc_map[doc] = doc_map.get(doc, 0) + 1

    return [{"document": k, "chunks": v} for k, v in doc_map.items()]

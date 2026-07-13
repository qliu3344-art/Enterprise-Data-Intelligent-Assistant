"""向量存储：ChromaDB 持久化 + 自动索引管理。

启动时检查文档是否变更（通过 checksum），自动决定是否重建索引。
支持按 source（文档名）维度删除和更新。
"""

import hashlib
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


def _compute_docs_checksum(documents) -> str:
    """计算所有文档内容的 checksum，用于判断是否需要重建索引。"""
    hasher = hashlib.md5()
    for doc in sorted(documents, key=lambda d: d.file_name):
        hasher.update(doc.content.encode("utf-8"))
    return hasher.hexdigest()


def _checksum_path() -> Path:
    """获取 checksum 缓存文件路径。"""
    return CHROMA_PATH / ".docs_checksum"


def needs_reindex() -> bool:
    """检查文档是否发生了变更，需要重建索引。"""
    documents = load_all_documents()
    if not documents:
        return False

    current_checksum = _compute_docs_checksum(documents)
    checksum_file = _checksum_path()

    if not checksum_file.exists():
        return True

    stored = checksum_file.read_text().strip()
    return stored != current_checksum


def index_all(force: bool = False) -> dict:
    """加载文档 → 分块 → 向量化 → 存入 ChromaDB。

    Args:
        force: 是否强制重建索引（即使文档未变更）

    Returns:
        {"documents": int, "chunks": int, "reindexed": bool}
    """
    documents = load_all_documents()
    if not documents:
        return {"documents": 0, "chunks": 0, "reindexed": False}

    current_checksum = _compute_docs_checksum(documents)

    if not force and not needs_reindex():
        collection = get_collection()
        count = collection.count()
        logger.info(f"文档未变更，跳过索引（已有 {count} 个向量）")
        return {"documents": len(documents), "chunks": count, "reindexed": False}

    logger.info("开始重建索引...")

    # 分块
    chunks = chunk_all(documents)
    logger.info(f"共 {len(chunks)} 个 chunks，开始向量化...")

    # 向量化
    texts = [c.text for c in chunks]
    embeddings = embed_documents(texts)

    # 存入 ChromaDB
    collection = get_collection()

    # 清空旧数据
    try:
        client = _get_client()
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    global _collection
    _collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    ids = [f"chunk_{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "doc_title": c.doc_title,
            "file_name": c.file_name,
            "chapter": c.chapter[:200] if c.chapter else "",
            "section": c.section[:200] if c.section else "",
        }
        for c in chunks
    ]

    # 分批写入，避免一次性写入过多
    batch_size = 50
    for i in range(0, len(ids), batch_size):
        end = min(i + batch_size, len(ids))
        _collection.add(
            ids=ids[i:end],
            embeddings=embeddings[i:end],
            documents=texts[i:end],
            metadatas=metadatas[i:end],
        )

    # 保存 checksum
    os.makedirs(str(CHROMA_PATH), exist_ok=True)
    _checksum_path().write_text(current_checksum)

    logger.info(f"索引完成: {len(documents)} 份文档 → {len(chunks)} chunks")
    return {"documents": len(documents), "chunks": len(chunks), "reindexed": True}


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

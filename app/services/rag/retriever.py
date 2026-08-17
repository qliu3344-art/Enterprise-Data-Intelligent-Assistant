"""混合检索器：BM25（关键词）+ 向量（语义）+ RRF 融合。

融合策略：
  - BM25 检索 top 20：精确匹配加班、异常、部门等关键词
  - 向量检索 top 20：语义相似度匹配
  - RRF（Reciprocal Rank Fusion）合并排序，k=60
  - 最终返回 top_k 条结果
"""

import re
from typing import List

from rank_bm25 import BM25Okapi

from app.logger import logger
from app.services.rag.embedder import embed_query, embed_documents
from app.services.rag.vector_store import search as vector_search


def _tokenize(text: str) -> List[str]:
    """中文 bigram + 英文单词级 混合分词。

    中文：字符级 bigram + unigram，适合短文本检索。
    英文：保留完整单词（以空白/标点分隔），不做字母级拆分。
    """
    tokens: List[str] = []

    # 按语言边界切分：中文连续块 vs 英文/数字连续块
    segments = re.split(r"([一-鿿]+)", text)

    for seg in segments:
        if not seg.strip():
            continue
        if re.match(r"[一-鿿]+", seg):
            # 中文块：bigram + unigram
            cleaned = seg.replace(" ", "")
            for i in range(len(cleaned) - 1):
                tokens.append(cleaned[i:i + 2])
            for ch in cleaned:
                tokens.append(ch)
        else:
            # 英文/数字块：按单词拆分
            words = re.findall(r"[a-zA-Z0-9]+", seg)
            tokens.extend(w.lower() for w in words)

    return tokens


class HybridRetriever:
    """混合检索器：BM25 + 向量 + RRF。"""

    def __init__(self):
        self._bm25: BM25Okapi | None = None
        self._corpus: List[str] = []
        self._metadatas: List[dict] = []
        self._text_to_idx: dict[str, int] = {}  # 文本 → corpus index 快速反查
        self._initialized = False

    def _ensure_initialized(self):
        """延迟初始化 BM25 索引（首次检索时构建）。"""
        if self._initialized:
            return

        from app.services.rag.vector_store import get_collection

        collection = get_collection()
        if collection.count() == 0:
            logger.warning("ChromaDB 为空，请先执行 /rag/reindex")
            self._initialized = True
            return

        result = collection.get(include=["documents", "metadatas"])
        self._corpus = result["documents"] or []
        self._metadatas = result["metadatas"] or []

        # 构建文本 → 索引反查表，向量结果回映射从 O(n²) → O(n)
        self._text_to_idx = {text: i for i, text in enumerate(self._corpus)}

        tokenized = [_tokenize(doc) for doc in self._corpus]
        self._bm25 = BM25Okapi(tokenized)
        self._initialized = True
        logger.info(f"BM25 索引就绪: {len(self._corpus)} 篇文档")

    def retrieve(self, query: str, top_k: int = 5) -> List[dict]:
        """混合检索主入口。

        Args:
            query: 用户查询
            top_k: 返回结果数量

        Returns:
            [{"text": str, "metadata": dict, "score": float, "bm25_rank": int, "vector_rank": int}, ...]
        """
        self._ensure_initialized()

        if not self._corpus:
            return []

        # —— BM25 检索 ——
        tokenized_query = _tokenize(query)
        bm25_scores = self._bm25.get_scores(tokenized_query)
        bm25_ranked = sorted(
            enumerate(bm25_scores), key=lambda x: x[1], reverse=True
        )[:20]

        # —— 向量检索 ——
        query_vec = embed_query(query)
        vector_hits = vector_search(query_vec, top_k=20)

        # 向量结果回映射 corpus index（O(1) 反查，代替原来的 O(n) 线性搜索）
        vector_ranked = []
        for hit in vector_hits:
            idx = self._text_to_idx.get(hit["text"])
            if idx is not None:
                vector_ranked.append((idx, hit["score"]))

        # —— RRF 融合 ——
        k = 60
        rrf_scores: dict[int, float] = {}

        for rank, (idx, score) in enumerate(bm25_ranked):
            rrf_scores[idx] = rrf_scores.get(idx, 0) + 1.0 / (k + rank + 1)

        for rank, (idx, score) in enumerate(vector_ranked):
            rrf_scores[idx] = rrf_scores.get(idx, 0) + 1.0 / (k + rank + 1)

        # 按 RRF 分数降序排列
        sorted_indices = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for idx, rrf_score in sorted_indices[:top_k]:
            results.append({
                "text": self._corpus[idx],
                "metadata": self._metadatas[idx] if idx < len(self._metadatas) else {},
                "score": round(rrf_score, 4),
            })

        return results

    def refresh(self):
        """强制刷新 BM25 索引（文档更新后调用）。"""
        self._bm25 = None
        self._corpus = []
        self._metadatas = []
        self._text_to_idx = {}
        self._initialized = False
        logger.info("BM25 索引已刷新")


# 全局单例
_retriever: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    """获取全局混合检索器实例。"""
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever


def retrieve(query: str, top_k: int = 5) -> List[dict]:
    """便捷函数：混合检索。"""
    return get_retriever().retrieve(query, top_k)


def refresh_retriever():
    """便捷函数：刷新检索器索引。"""
    if _retriever:
        _retriever.refresh()

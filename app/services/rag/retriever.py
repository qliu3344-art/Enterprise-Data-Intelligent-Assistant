"""混合检索器：BM25（关键词）+ 向量（语义）+ RRF 融合。

融合策略：
  - BM25 检索 top 20：精确匹配加班、异常、部门等关键词
  - 向量检索 top 20：语义相似度匹配
  - RRF（Reciprocal Rank Fusion）合并排序，k=60
  - 最终返回 top_k 条结果
"""

from typing import List

from rank_bm25 import BM25Okapi

from app.logger import logger
from app.services.rag.embedder import embed_query, embed_documents
from app.services.rag.vector_store import search as vector_search


def _tokenize(text: str) -> List[str]:
    """中文分词：使用字符级 bigram + 单字作为 fallback。

    不使用 jieba 等第三方分词库，避免依赖膨胀。
    Bigram 分词对于中文短文本检索效果足够好。
    """
    # 移除标点和空白
    cleaned = ""
    for ch in text:
        if ch.isalnum() or "一" <= ch <= "鿿":
            cleaned += ch
        else:
            cleaned += " "

    tokens = []
    # Bigram
    for i in range(len(cleaned) - 1):
        bigram = cleaned[i:i + 2]
        if not bigram.isspace():
            tokens.append(bigram)
    # Unigram（中文字符）
    for ch in cleaned:
        if "一" <= ch <= "鿿":
            tokens.append(ch)

    return tokens


class HybridRetriever:
    """混合检索器：BM25 + 向量 + RRF。"""

    def __init__(self):
        self._bm25: BM25Okapi | None = None
        self._corpus: List[str] = []
        self._metadatas: List[dict] = []
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

        # 向量结果需要映射回 corpus index
        vector_ranked = []
        for hit in vector_hits:
            hit_text = hit["text"]
            try:
                idx = self._corpus.index(hit_text)
                vector_ranked.append((idx, hit["score"]))
            except ValueError:
                # 文本不完全匹配（不应该发生），跳过
                pass

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

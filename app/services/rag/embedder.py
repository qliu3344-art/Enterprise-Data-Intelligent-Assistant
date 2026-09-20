"""Embedding 服务：使用 BGE-small-zh-v1.5 本地模型。

模型首次使用时会自动从 HuggingFace 下载（~100MB）。
查询时需要加 BGE 标准前缀以保证检索效果。
"""

from typing import List

from app.logger import logger

# BGE 模型要求的查询前缀
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

_embedding_model = None


def _get_model():
    """延迟加载模型（单例）。

    优先使用本地缓存。在国内网络环境下，设置 HF_ENDPOINT 环境变量可
    加速首次下载：$env:HF_ENDPOINT = "https://hf-mirror.com"
    """
    global _embedding_model
    if _embedding_model is None:
        import os
        from sentence_transformers import SentenceTransformer

        model_name = "BAAI/bge-small-zh-v1.5"
        logger.info(f"加载 Embedding 模型: {model_name}...")

        # 检查本地缓存是否存在
        from huggingface_hub import snapshot_download

        cache_dir = os.path.join(
            os.path.expanduser("~"), ".cache", "huggingface", "hub"
        )
        model_dir = os.path.join(
            cache_dir, "models--BAAI--bge-small-zh-v1.5"
        )
        has_local_cache = os.path.isdir(model_dir)

        if has_local_cache:
            logger.info("检测到本地缓存，使用离线模式加载")
            _embedding_model = SentenceTransformer(
                model_name, local_files_only=True
            )
        else:
            logger.info("首次使用，从 HuggingFace 下载模型（~100MB）...")
            _embedding_model = SentenceTransformer(model_name)
        logger.info("Embedding 模型加载完成")
    return _embedding_model


def embed_documents(texts: List[str]) -> List[List[float]]:
    """对文档文本进行向量化（不加前缀）。

    Args:
        texts: 待向量化的文本列表

    Returns:
        向量列表，每个向量 512 维
    """
    model = _get_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.tolist()


def embed_query(query: str) -> List[float]:
    """对查询文本进行向量化（自动加 BGE 前缀）。

    Args:
        query: 原始查询文本

    Returns:
        512 维向量
    """
    model = _get_model()
    embedding = model.encode(
        QUERY_PREFIX + query,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return embedding.tolist()


def get_embedding_dim() -> int:
    """返回当前模型的向量维度。"""
    model = _get_model()
    return model.get_sentence_embedding_dimension()

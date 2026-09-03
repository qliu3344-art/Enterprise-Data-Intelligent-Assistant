"""业务口径记忆：业务规则 / 指标口径的向量化存储。

复用 RAG 已搭建的 BGE Embedding + ChromaDB 基础设施，使用独立的
collection（business_conventions），与制度文档（enterprise_policies）隔离。

定位说明（为什么是「口径」而不是「用户画像」）：
  企业数据查询的数据是客观、公开、透明的，不存在「用户偏好」。真正值得
  跨会话沉淀的是「业务口径 / 规则」——用户/业务交代过的指标定义、字段
  含义、业务规则，说一次长期生效。例如「合同额指含税合同金额」「异常率
  = 异常记录数 / 总记录数」。

  口径是全局业务规则（非用户个性化），故全局共享、不按用户隔离。这同时
  消除了「跨用户串记忆」的隐患——口径本来就该所有人一致。

核心原则：按需注入，不是全量注入 —— 只召回最相关的口径拼进上下文，
不无脑塞满。
"""

import time
import uuid

from langchain_core.messages import SystemMessage

from app.logger import logger
from app.services.rag.embedder import embed_documents, embed_query
from app.services.rag.vector_store import _get_client

# 独立 collection，与制度文档隔离
CONVENTION_COLLECTION = "business_conventions"

# 全局口径上限：超出后删除最旧的口径（防止无界增长）
MAX_CONVENTIONS = 200

# 去重阈值：相似度超过此值视为重复口径，不重复写入
DEDUP_THRESHOLD = 0.9

# 口径信号词：命中才触发 LLM 提取，避免每轮都消耗一次 LLM 调用
CONVENTION_HINTS = (
    "指的是", "记住", "口径", "定义", "以后", "叫做", "称为",
    "我们内部", "约定", "规则",
)

# 口径提取 Prompt
EXTRACT_PROMPT = """请判断下面这段用户与数据助手的对话中，用户是否交代了新的「业务口径 / 规则」（如指标定义、字段含义、业务约定）。

示例口径：
- 合同额指含税合同金额
- 异常率 = 异常记录数 / 总记录数
- 销售部只统计正式员工，不含外包

要求：
1. 如果没有交代任何新口径，只输出「无」
2. 如果有，逐条输出，每条一行，不要编号、不要解释
3. 只输出口径本身，简洁准确

用户问题：
{question}

助手回答：
{answer}

输出："""

_collection = None


def _get_convention_collection():
    """获取或创建业务口径 collection（懒加载单例）。"""
    global _collection
    if _collection is None:
        client = _get_client()
        _collection = client.get_or_create_collection(
            name=CONVENTION_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _has_convention_hint(text: str) -> bool:
    """判断文本是否含有「交代口径」的信号词。"""
    if not text:
        return False
    return any(hint in text for hint in CONVENTION_HINTS)


def _evict_oldest(collection):
    """删除最旧的一条口径（按 created_at 升序取最早一条）。"""
    try:
        existing = collection.get(include=["metadatas"])
        if not existing or not existing.get("ids"):
            return
        items = list(zip(existing["ids"], existing["metadatas"]))
        items.sort(key=lambda x: (x[1] or {}).get("created_at", 0))
        oldest_id = items[0][0]
        collection.delete(ids=[oldest_id])
        logger.info(f"口径达上限，删除最旧一条: {oldest_id}")
    except Exception as e:
        logger.warning(f"删除最旧口径失败: {e}")


def save_convention(content: str, metadata: dict | None = None) -> bool:
    """保存一条业务口径（去重 + 上限控制）。

    Args:
        content: 口径文本（如「合同额指含税合同金额」）
        metadata: 额外元数据

    Returns:
        是否保存成功（重复口径会返回 False）
    """
    content = (content or "").strip()
    if not content:
        return False

    try:
        collection = _get_convention_collection()
        embedding = embed_documents([content])[0]

        # 去重：检索最相似的一条，相似度过高则不重复写
        if collection.count() > 0:
            dup = collection.query(
                query_embeddings=[embedding],
                n_results=1,
                include=["distances"],
            )
            if dup.get("distances") and dup["distances"][0]:
                distance = dup["distances"][0][0]
                similarity = 1.0 - (distance / 2.0)
                if similarity >= DEDUP_THRESHOLD:
                    logger.info(
                        f"口径重复（相似度 {similarity:.2f}），跳过: {content[:40]}..."
                    )
                    return False

        # 上限：超出则删除最旧一条，再写入
        if collection.count() >= MAX_CONVENTIONS:
            _evict_oldest(collection)

        convention_id = f"conv_{uuid.uuid4().hex}"
        meta = {"type": "convention", "created_at": int(time.time())}
        if metadata:
            meta.update(metadata)
        collection.add(
            ids=[convention_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[meta],
        )
        logger.info(f"业务口径已保存: {content[:50]}...")
        return True
    except Exception as e:
        logger.error(f"保存业务口径失败: {e}")
        return False


def recall_conventions(query: str, top_k: int = 3) -> list[dict]:
    """按语义召回与当前问题最相关的业务口径。

    Returns:
        [{"text": str, "metadata": dict, "score": float}, ...]
    """
    try:
        collection = _get_convention_collection()
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
        logger.error(f"召回业务口径失败: {e}")
        return []


def build_convention_message(question: str, top_k: int = 3) -> SystemMessage | None:
    """召回相关业务口径，组装成 SystemMessage（无相关口径时返回 None）。"""
    conventions = recall_conventions(question, top_k=top_k)
    if not conventions:
        return None

    lines = [f"- {c['text']}" for c in conventions]
    content = (
        "以下是已确认的业务口径/规则，回答与计算时必须遵循（不要主动提及口径本身）：\n"
        + "\n".join(lines)
    )
    return SystemMessage(content=content)


def extract_conventions(question: str, answer: str) -> list[str]:
    """从一轮问答中提取新交代的业务口径。

    先用信号词预筛，命中才调 LLM 提取，避免每轮都消耗一次 LLM 调用。

    Returns:
        提取到的口径文本列表（可能为空）。
    """
    text = f"{question}\n{answer}"
    if not _has_convention_hint(text):
        return []

    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_core.messages import HumanMessage

    from app.config import settings

    prompt = EXTRACT_PROMPT.format(question=question[:500], answer=answer[:500])
    try:
        llm = ChatTongyi(
            model=settings.LLM_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.0,
            max_tokens=300,
        )
        resp = llm.invoke([HumanMessage(content=prompt)])
        raw = (resp.content or "").strip()
        if not raw or raw in ("无", "空", "none", "None", "[]"):
            return []
        # 逐行拆分，去掉可能的 "- " 前缀，过滤空行
        lines = [
            ln.strip().lstrip("- ").strip()
            for ln in raw.splitlines()
            if ln.strip()
        ]
        return [ln for ln in lines if ln]
    except Exception as e:
        logger.warning(f"业务口径提取失败: {e}")
        return []

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

生命周期：写入侧「去重/更新」（相似度 ≥0.9 视为同一条，覆盖 + 记 updated_at，
0.9~0.95 灰色地带调 LLM 确认是否改口）＋「主动遗忘」（last_accessed + LRU 淘汰
＋超期 STALE_DAYS 过期删除）＋「上限」（MAX_CONVENTIONS 满员删最久未访问）。
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

# 去重/更新阈值：相似度超过此值视为「同一条口径」（重复说或改口）
DEDUP_THRESHOLD = 0.9
# 直接覆盖阈值：相似度超过此值直接判「更新」，无需 LLM 确认
CONFLICT_THRESHOLD = 0.95
# 主动遗忘：超过 N 天未访问的口径过期删除
STALE_DAYS = 90

# 召回相似度下限：低于此值视为「与当前问题无关」，既不返回也不计入访问。
# 不设此值的话，任何问题都会召回 top_k 条（矮子里拔将军）并给它们续命，
# 导致 STALE_DAYS 过期永远不触发、LRU 淘汰的也不是真正最久未用的口径。
RECALL_MIN_SCORE = 0.35

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


def _last_active_at(meta: dict | None, default: int) -> int:
    """取口径的最后活跃时间。

    优先 last_accessed；旧数据（本次改动前写入的）没有该字段，回退 created_at。
    两处淘汰逻辑共用此函数，避免回退口径不一致。
    """
    m = meta or {}
    return m.get("last_accessed", m.get("created_at", default))


def _evict_oldest(collection):
    """删除最久未访问的一条口径（按 last_accessed 升序，LRU 淘汰）。"""
    try:
        existing = collection.get(include=["metadatas"])
        if not existing or not existing.get("ids"):
            return
        items = list(zip(existing["ids"], existing["metadatas"]))
        items.sort(key=lambda x: _last_active_at(x[1], 0))
        oldest_id = items[0][0]
        collection.delete(ids=[oldest_id])
        logger.info(f"口径达上限，删除最久未访问一条: {oldest_id}")
    except Exception as e:
        logger.warning(f"删除最久未访问口径失败: {e}")


def _evict_stale(collection):
    """删除超过 STALE_DAYS 天未访问的口径（主动遗忘）。"""
    try:
        existing = collection.get(include=["metadatas"])
        if not existing or not existing.get("ids"):
            return
        now = int(time.time())
        stale_seconds = STALE_DAYS * 24 * 3600
        stale_ids = [
            i
            for i, m in zip(existing["ids"], existing["metadatas"])
            if now - _last_active_at(m, now) > stale_seconds
        ]
        if stale_ids:
            collection.delete(ids=stale_ids)
            logger.info(f"过期口径清理：删除 {len(stale_ids)} 条（>{STALE_DAYS} 天未访问）")
    except Exception as e:
        logger.warning(f"过期口径清理失败: {e}")


def _confirm_update(old_text: str, new_text: str) -> bool:
    """灰色地带（相似度 0.9~0.95）判定：新口径是「同一条的改口更新」还是「两条不同规则」。

    向量相似度只看「像不像」，分不清「是不是反着改」，故此处调 LLM 定性。

    判定取向：只有明确得到「更新」才覆盖，语义不明一律保守判「不同」保留两条。
    覆盖是不可逆的（旧口径永久消失），共存至少人能看出冲突。

    例外：LLM 调用失败时按「更新」处理 —— 此时拿不到任何信息，宁可覆盖，
    避免新旧口径同时被召回造成矛盾。
    """
    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_core.messages import HumanMessage

    from app.config import settings

    prompt = (
        "下面两条「业务口径」文本语义高度相似。请判断：第二条是「对第一条的改口/更新」"
        "（同一指标，用户修正了说法），还是「两条不同但相似的口径」（两个不同规则）？\n"
        "只输出「更新」或「不同」两个词之一。\n\n"
        f"第一条：{old_text}\n"
        f"第二条：{new_text}\n\n"
        "输出："
    )
    try:
        llm = ChatTongyi(
            model=settings.LLM_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.0,
            max_tokens=20,
        )
        resp = llm.invoke([HumanMessage(content=prompt)])
        raw = (resp.content or "").strip()

        # 先判「不同」：模型可能输出「不相同」等变体 —— 含「相同」但不含「不同」，
        # 语义是「不同」，若用 "不同" not in raw 会被反向误判为更新
        if "不同" in raw:
            return False
        if "更新" in raw:
            return True

        # 两个关键词都没命中：语义不明，保守判「不同」保留两条，不覆盖
        logger.warning(f"口径冲突判定语义不明（{raw[:30]}），保守按「不同」保留两条")
        return False
    except Exception as e:
        logger.warning(f"口径冲突确认失败，默认按更新处理: {e}")
        return True


def save_convention(content: str, metadata: dict | None = None) -> bool:
    """保存一条业务口径（去重/更新 + 主动遗忘 + 上限控制）。

    Args:
        content: 口径文本（如「合同额指含税合同金额」）
        metadata: 额外元数据

    Returns:
        是否保存成功。重复口径会「覆盖」旧口径（更新）并返回 True；
        判定为两条不同规则时作为新口径写入。
    """
    content = (content or "").strip()
    if not content:
        return False

    try:
        collection = _get_convention_collection()
        embedding = embed_documents([content])[0]

        # 去重/更新：检索最相似的一条，相似度过高视为「同一条口径」，覆盖而非跳过
        # （重复说→覆盖成一样，无害；改口→覆盖成新口径，正确）
        if collection.count() > 0:
            dup = collection.query(
                query_embeddings=[embedding],
                n_results=1,
                include=["distances", "documents", "metadatas"],
            )
            if dup.get("distances") and dup["distances"][0]:
                distance = dup["distances"][0][0]
                similarity = 1.0 - (distance / 2.0)
                if similarity >= DEDUP_THRESHOLD:
                    old_id = dup["ids"][0][0]
                    old_text = dup["documents"][0][0] if dup.get("documents") else ""
                    old_meta = dup["metadatas"][0][0] if dup.get("metadatas") else {}
                    # 灰色地带（0.9~0.95）：向量只判断「像不像」，调 LLM 确认是「改口更新」还是「两条不同规则」
                    if similarity >= CONFLICT_THRESHOLD or _confirm_update(old_text, content):
                        now = int(time.time())
                        # 保留旧口径的其余字段（如调用方传入的 source / department），
                        # 只刷新时间戳 —— 原来整体重建 metadata 会把未知字段静默抹掉
                        merged_meta = dict(old_meta or {})
                        merged_meta.update({
                            "type": "convention",
                            "created_at": merged_meta.get("created_at", now),
                            "updated_at": now,
                            "last_accessed": now,
                        })
                        collection.update(
                            ids=[old_id],
                            embeddings=[embedding],
                            documents=[content],
                            metadatas=[merged_meta],
                        )
                        logger.info(f"口径更新（相似度 {similarity:.2f}）: {content[:40]}...")
                        return True
                    # 判定为「两条不同规则」：落入下方按新口径写入

        # 主动遗忘：先清理过期口径，再做上限淘汰
        _evict_stale(collection)
        if collection.count() >= MAX_CONVENTIONS:
            _evict_oldest(collection)

        convention_id = f"conv_{uuid.uuid4().hex}"
        now = int(time.time())
        meta = {"type": "convention", "created_at": now, "updated_at": now, "last_accessed": now}
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
        # 相似度达标的命中（id, metadata），用于后续刷新访问时间
        accessed: list[tuple[str, dict]] = []

        if results.get("ids") and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                distance = results["distances"][0][i] if results["distances"] else 0
                # Cosine distance ∈ [0,2]，转换为相似度 ∈ [0,1]
                similarity = 1.0 - (distance / 2.0) if distance else 1.0
                if similarity < RECALL_MIN_SCORE:
                    continue

                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                hits.append({
                    "text": results["documents"][0][i] if results["documents"] else "",
                    "metadata": meta,
                    "score": round(similarity, 4),
                })
                accessed.append((results["ids"][0][i], dict(meta or {})))

            # 刷新命中口径的 last_accessed（供 LRU 淘汰与超期遗忘使用）
            # 未达阈值的不刷新 —— 否则无关问题也会给口径续命，主动遗忘形同虚设
            if accessed:
                try:
                    now = int(time.time())
                    for _, m in accessed:
                        m["last_accessed"] = now
                    collection.update(
                        ids=[i for i, _ in accessed],
                        metadatas=[m for _, m in accessed],
                    )
                except Exception as e:
                    logger.warning(f"刷新口径访问时间失败: {e}")

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

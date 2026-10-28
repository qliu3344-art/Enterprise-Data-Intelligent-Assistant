"""向量存储：pgvector，与业务数据同库。

替代原 ChromaDB 实现。对外契约保持不变（index_all / needs_reindex /
get_document_stats / search 的签名与返回结构一字未改），消费方无需改动。

三处刻意的差异：

1. **相似度标度是 `1 - distance/2`，不是 `1 - distance`。**
   Chroma 的 hnsw:space=cosine 距离是 d = 1 - cos ∈ [0,2]，而 pgvector 的
   `<=>` 返回完全相同的量。旧代码的 `1 - d/2` 展开就是 `0.5 + 0.5*cos` ——
   正交文本得 0.5，不是 0。memory_store 那几个阈值全标定在这个标度上。

2. **不建向量索引。** 理由见 app/models/doc_chunk.py。

3. **增量重建是单事务。** 旧实现是「先删旧向量 → 再向量化 → 最后写 checksum
   文件」，中间抛异常（embed 时 OOM / 模型下载失败 / Ctrl-C）就会留下
   「向量已删、checksum 还是旧的」，下次启动据此判定「没变」，这份文档
   从此再也不会被索引。同库同事务把这条失败路径变成回滚。
"""

import hashlib
import json
from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, func, insert, literal, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import Base, SessionLocal, engine
from app.logger import logger
from app.models.business_convention import BusinessConvention
from app.models.doc_chunk import DocChunk, DocIndexState
from app.services.rag.chunker import CHUNKER_VERSION, chunk_all
from app.services.rag.document_loader import load_all_documents, parse_effective_date
from app.services.rag.embedder import embed_documents

_schema_ready = False


def ensure_schema() -> None:
    """幂等建表：CREATE EXTENSION + 本模块用到的三张表。

    Chroma 的 get_or_create_collection 会自举存储，PostgreSQL 不会。而
    app/main.py 启动时调 index_all() 是包在 except 里只打 warning 的 ——
    表不存在会被静默吞掉：服务正常启动、/rag/health 报 not_indexed、
    没有任何错误日志。所以表必须在这里自举出来。
    """
    global _schema_ready
    if _schema_ready:
        return
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(
        bind=engine,
        tables=[
            DocChunk.__table__,
            DocIndexState.__table__,
            BusinessConvention.__table__,
        ],
    )
    _migrate_chunker_v2()
    _schema_ready = True


def _migrate_chunker_v2() -> None:
    """给已存在的 doc_chunks 补条款级分块所需的列（幂等）。

    create_all 只建新表、不改旧表；线上库已经有章级 chunk，必须显式 ALTER。
    旧行的 chunk_key 为 NULL，部分唯一索引允许它们共存；下一次 index_all
    会因为 _doc_checksum 纳入 CHUNKER_VERSION 而整份重建，把它们替换掉。
    """
    with engine.begin() as conn:
        for ddl in (
            "ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS chunk_key VARCHAR(255)",
            "ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS doc_id VARCHAR(64)",
            "ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS version VARCHAR(64)",
            "ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS clause_no VARCHAR(64)",
            "ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS parent_key VARCHAR(255)",
            "ALTER TABLE doc_chunks DROP CONSTRAINT IF EXISTS uq_doc_chunks_file_chunk",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_chunks_file_key "
            "ON doc_chunks (file_name, chunk_key) WHERE chunk_key IS NOT NULL",
        ):
            conn.execute(text(ddl))


# ── 相似度标度 ──────────────────────────────────────────────────────
# 与旧 Chroma 实现逐位一致，改这里等于改掉 memory_store 三个阈值的语义：
#   Chroma hnsw:space=cosine 的 distance = 1 - cos_sim ∈ [0,2]
#   pgvector 的 `<=>` 返回完全相同的量
#   旧代码 score = 1 - distance/2 = 0.5 + 0.5*cos_sim ∈ [0,1]
# 即正交文本得 0.5、完全相同得 1.0。去掉 /2 会让所有阈值整体移位一倍。
def similarity_expr(distance):
    """把 pgvector 的余弦距离换算成与旧实现一致的相似度。

    memory_store 也导入这个函数——它那几个阈值和这里是同一套标度，
    两处必须用同一个表达式，不能各写一份。
    """
    return literal(1.0) - distance / literal(2.0)


def _live_filter():
    """生效日期过滤：只返回当前已生效的条款。

    NULL 视为「长期有效」（解析失败时的兜底）。写成裸的
    `effective_date <= CURRENT_DATE` 会把解析失败的制度静默排除出检索。

    ⚠️ search() 与 dump_corpus() 必须共用这一个函数：两条召回通道若过滤
    条件不一致，BM25 和向量就指向不同的语料，RRF 融合的结果不可信。
    """
    return or_(
        DocChunk.effective_date.is_(None),
        DocChunk.effective_date <= func.current_date(),
    )


# ── 指纹 ────────────────────────────────────────────────────────────
def _chunk_checksum(content: str) -> str:
    """单条 chunk 正文的 md5。"""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _doc_checksum(document) -> str:
    """整份文档的指纹：正文 + frontmatter 元数据 + 分块器版本。

    必须把元数据算进去 —— 生效日期来自 frontmatter，正文没变而 frontmatter
    变了时（制度改版只改生效日期），只哈希正文会判定「没变」，effective_date
    列就永远停在旧值上。

    必须把 CHUNKER_VERSION 算进去 —— 改分块逻辑但源文件没变时，只比正文会
    判定「没变」，旧索引原样留着，表现是「改了点不生效」。
    """
    payload = (
        document.content
        + "\n"
        + json.dumps(document.metadata, ensure_ascii=False, sort_keys=True)
        + "\n"
        + CHUNKER_VERSION
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _chunk_key(c) -> tuple:
    """chunk 在文档内的稳定身份：条款标题，不是序号。"""
    return (c.file_name, c.chunk_key)


def _chunk_metadata(c) -> dict:
    """chunk 的元数据（对外契约，service.py 依赖 doc_title/file_name/chapter/section）。"""
    return {
        "doc_title": c.doc_title,
        "file_name": c.file_name,
        "doc_id": str(c.metadata.get("文档编号", "") or ""),
        "version": str(c.metadata.get("版本号", "") or ""),
        "chapter": c.chapter or "",
        "section": c.section or "",
        "clause_no": c.clause_no or "",
        "chunk_key": c.chunk_key or "",
        "parent_key": c.parent_key or "",
    }


# ── 索引状态 ────────────────────────────────────────────────────────
def _load_stored_checksums(db) -> dict:
    """读取已索引文档的 {file_name: doc_checksum}。"""
    rows = db.execute(
        select(DocIndexState.file_name, DocIndexState.content_hash)
    ).all()
    return {r.file_name: r.content_hash for r in rows}


def needs_reindex() -> bool:
    """检查是否有文档新增、删除或内容变更（per-document 比对）。"""
    ensure_schema()
    documents = load_all_documents()
    if not documents:
        return False
    current = {d.file_name: _doc_checksum(d) for d in documents}
    db = SessionLocal()
    try:
        return current != _load_stored_checksums(db)
    finally:
        SessionLocal.remove()


def index_all(force: bool = False) -> dict:
    """加载文档 → 分块 → 向量化 → 写入 pgvector。

    force=False：per-document + per-chunk 两级增量。文档没变就整份跳过；
    文档变了但某条 chunk 正文的哈希没变，则直接复用旧向量、不重新向量化。

    force=True：全量重建。

    Returns:
        {"documents", "chunks", "reindexed", "added", "updated", "removed"}
    """
    ensure_schema()
    documents = load_all_documents()
    if not documents:
        return {"documents": 0, "chunks": 0, "reindexed": False,
                "added": 0, "updated": 0, "removed": 0}

    current = {d.file_name: _doc_checksum(d) for d in documents}

    db = SessionLocal()
    try:
        # ═══════════ 单事务开始：删旧与插新必须一起提交或一起回滚 ═══════════
        stored = _load_stored_checksums(db)

        if force:
            added, updated, removed = sorted(current), [], sorted(stored)
        else:
            added = [n for n in current if n not in stored]
            updated = [n for n in current if n in stored and current[n] != stored[n]]
            removed = [n for n in stored if n not in current]

        if not (force or added or updated or removed):
            total = db.scalar(select(func.count()).select_from(DocChunk)) or 0
            logger.info(f"文档未变更，跳过索引（已有 {total} 个向量）")
            return {"documents": len(documents), "chunks": total, "reindexed": False,
                    "added": 0, "updated": 0, "removed": 0}

        # ── 1. 分块（纯 CPU，不碰模型）──
        changed_names = set(added) | set(updated)
        changed_docs = [d for d in documents if d.file_name in changed_names]
        new_chunks = chunk_all(changed_docs)
        new_hash = {_chunk_key(c): _chunk_checksum(c.text) for c in new_chunks}

        # ── 2. chunk 级哈希比对 ──
        # 必须在调 embed 之前做：chunk 哈希唯一的用处就是跳过向量化，
        # 而 SQL 侧的 ON CONFLICT ... WHERE hash <> 要求你先把新向量算出来
        # 才能写那条语句 —— 模型已经跑完了，白算。
        old_map = {}
        if changed_names:
            rows = db.execute(
                select(
                    DocChunk.file_name, DocChunk.chunk_key,
                    DocChunk.content_hash, DocChunk.embedding, DocChunk.created_at,
                ).where(DocChunk.file_name.in_(changed_names))
            ).all()
            old_map = {(r.file_name, r.chunk_key): r for r in rows}

        need_embed = []
        vectors: dict = {}
        created: dict = {}
        for c in new_chunks:
            key = _chunk_key(c)
            prev = old_map.get(key)
            if prev is not None and prev.content_hash == new_hash[key]:
                # 条款正文没变 → 复用旧向量，连首次索引时间一起继承
                vectors[key] = [float(x) for x in prev.embedding]
                created[key] = prev.created_at
            else:
                need_embed.append(c)
                created[key] = datetime.now()

        if need_embed:
            embedded = embed_documents([c.text for c in need_embed])
            vectors.update({_chunk_key(c): v for c, v in zip(need_embed, embedded)})

        # ── 3. 删旧 ──
        if force:
            # 用 DELETE 而不是 TRUNCATE：TRUNCATE 要 ACCESS EXCLUSIVE 锁，
            # 会挡住并发读；DELETE 走 MVCC，读到的要么全是旧、要么全是新。
            db.execute(delete(DocChunk))
            db.execute(delete(DocIndexState))
        elif changed_names or removed:
            db.execute(
                delete(DocChunk).where(
                    DocChunk.file_name.in_(changed_names | set(removed))
                )
            )
        if removed and not force:
            # 已移除文档的状态行必须一起删。留着的话 next run 仍会把它算进
            # `removed`，needs_reindex() 就永远返回 True（旧实现靠整份覆写
            # checksum 文件顺带解决了这件事，这里要显式做）。
            db.execute(
                delete(DocIndexState).where(DocIndexState.file_name.in_(removed))
            )

        # ── 4. 插新 ──
        if new_chunks:
            now = datetime.now()
            changed_keys = {_chunk_key(c) for c in need_embed}
            db.execute(insert(DocChunk), [
                {
                    "file_name": c.file_name,
                    "chunk_key": c.chunk_key,
                    "chunk_index": c.chunk_index,
                    "doc_title": c.doc_title,
                    "doc_id": str(c.metadata.get("文档编号", "") or "")[:64],
                    "version": str(c.metadata.get("版本号", "") or "")[:64],
                    "chapter": c.chapter or "",
                    "section": c.section or "",
                    "clause_no": c.clause_no or "",
                    "parent_key": c.parent_key or "",
                    "content": c.text,
                    "content_hash": new_hash[_chunk_key(c)],
                    "embedding": vectors[_chunk_key(c)],
                    "effective_date": parse_effective_date(c.metadata.get("生效日期")),
                    "created_at": created[_chunk_key(c)],
                    # 只有正文真正变过才推进 updated_at，否则继承首次索引时间；
                    # 这张表每次改版都是整体 delete + insert，updated_at 要是
                    # 跟着每次重建走，就不能当「内容变更水位」用了。
                    "updated_at": now if _chunk_key(c) in changed_keys
                                  else created[_chunk_key(c)],
                }
                for c in new_chunks
            ])

        # ── 5. 文档级状态 ──
        for name in (sorted(current) if force else added + updated):
            cnt = sum(1 for c in new_chunks if c.file_name == name)
            # on_conflict_do_update 是 PG 方言的，通用 insert() 上没有
            stmt = pg_insert(DocIndexState).values(
                file_name=name, content_hash=current[name],
                indexed_at=datetime.now(),
            )
            if not force:
                stmt = stmt.on_conflict_do_update(
                    index_elements=[DocIndexState.file_name],
                    set_={"content_hash": current[name], "indexed_at": datetime.now()},
                )
            db.execute(stmt)

        total = db.scalar(select(func.count()).select_from(DocChunk)) or 0
        db.commit()
        # ═══════════ 单事务提交 ═══════════

    except Exception:
        db.rollback()
        raise
    finally:
        SessionLocal.remove()

    logger.info(
        f"增量索引完成: 新增 {len(added)} / 更新 {len(updated)} / 删除 {len(removed)}，"
        f"当前 {total} 个向量"
    )
    return {"documents": len(documents), "chunks": total, "reindexed": True,
            "added": len(added), "updated": len(updated), "removed": len(removed)}


# ── 查询 ────────────────────────────────────────────────────────────
def search(query_embedding: List[float], top_k: int = 20) -> list[dict]:
    """向量相似度检索。

    Returns:
        [{"id": int, "text": str, "metadata": dict, "score": float}, ...]
    """
    ensure_schema()
    distance = DocChunk.embedding.cosine_distance(query_embedding)

    stmt = (
        select(
            DocChunk.id,
            DocChunk.content,
            DocChunk.doc_title,
            DocChunk.file_name,
            DocChunk.doc_id,
            DocChunk.version,
            DocChunk.chapter,
            DocChunk.section,
            DocChunk.clause_no,
            DocChunk.chunk_key,
            DocChunk.parent_key,
            similarity_expr(distance).label("score"),
        )
        .where(_live_filter())
        # 按裸距离排序，不要按 score 降序：包一层算术会让表达式不可索引，
        # 从「能用索引」静默退化成全表扫描 + 排序，不报错。
        .order_by(distance)
        .limit(top_k)
    )

    db = SessionLocal()
    try:
        rows = db.execute(stmt).all()
    finally:
        SessionLocal.remove()

    return [
        {
            "id": r.id,
            "text": r.content,
            "metadata": {
                "doc_title": r.doc_title,
                "file_name": r.file_name,
                "doc_id": r.doc_id or "",
                "version": r.version or "",
                "chapter": r.chapter or "",
                "section": r.section or "",
                "clause_no": r.clause_no or "",
                "chunk_key": r.chunk_key or "",
                "parent_key": r.parent_key or "",
            },
            "score": round(float(r.score), 4),
        }
        for r in rows
    ]


def dump_corpus() -> list[dict]:
    """全量导出检索语料（替代旧的 get_collection().get(...)）。

    用途：
      - retriever 在内存里构建 BM25 倒排索引
      - RRF 融合后按 id 回填原文与元数据

    与 search() 复用同一个 _live_filter()，两条召回通道指向同一份语料；
    并用 ORDER BY id 保证顺序稳定，且必须带上 id —— 调用方用 id 做反查，
    不能用文本做键（同一句话可能在不同块里重复出现）。
    """
    ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                DocChunk.id, DocChunk.content, DocChunk.doc_title,
                DocChunk.file_name, DocChunk.doc_id, DocChunk.version,
                DocChunk.chapter, DocChunk.section, DocChunk.clause_no,
                DocChunk.chunk_key, DocChunk.parent_key,
            )
            .where(_live_filter())
            .order_by(DocChunk.id)
        ).all()
    finally:
        SessionLocal.remove()

    return [
        {
            "id": r.id,
            "text": r.content,
            "metadata": {
                "doc_title": r.doc_title,
                "file_name": r.file_name,
                "doc_id": r.doc_id or "",
                "version": r.version or "",
                "chapter": r.chapter or "",
                "section": r.section or "",
                "clause_no": r.clause_no or "",
                "chunk_key": r.chunk_key or "",
                "parent_key": r.parent_key or "",
            },
        }
        for r in rows
    ]


def fetch_parent_chunks(refs: set[tuple[str, str]]) -> dict[tuple[str, str], list[dict]]:
    """按 (file_name, parent_key) 取回整章的所有 chunk，供父子块回填。

    父块不单独建向量、不参与检索：检索仍然打在条款上，命中后再按 parent_key
    取回同章条款，只用于组装生成侧上下文。回填走同一个 _live_filter()，
    过期的章不会被拉回来。

    Returns:
        {(file_name, parent_key): [{"text", "metadata", "chunk_index"}, ...]}
        每组按 chunk_index 升序。
    """
    refs = {(f, p) for f, p in refs if p}
    if not refs:
        return {}

    ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                DocChunk.id, DocChunk.content, DocChunk.doc_title,
                DocChunk.file_name, DocChunk.doc_id, DocChunk.version,
                DocChunk.chapter, DocChunk.section, DocChunk.clause_no,
                DocChunk.chunk_key, DocChunk.parent_key, DocChunk.chunk_index,
            )
            .where(
                tuple_(DocChunk.file_name, DocChunk.parent_key).in_(sorted(refs)),
                _live_filter(),
            )
            .order_by(DocChunk.file_name, DocChunk.chunk_index)
        ).all()
    finally:
        SessionLocal.remove()

    grouped: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        grouped.setdefault((r.file_name, r.parent_key), []).append({
            "text": r.content,
            "chunk_index": r.chunk_index,
            "metadata": {
                "doc_title": r.doc_title,
                "file_name": r.file_name,
                "doc_id": r.doc_id or "",
                "version": r.version or "",
                "chapter": r.chapter or "",
                "section": r.section or "",
                "clause_no": r.clause_no or "",
                "chunk_key": r.chunk_key or "",
                "parent_key": r.parent_key or "",
            },
        })
    return grouped


def get_document_stats() -> list[dict]:
    """获取已索引文档的统计信息（按 doc_title 聚合）。"""
    ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(
            select(DocChunk.doc_title, func.count().label("chunks"))
            .group_by(DocChunk.doc_title)
            .order_by(DocChunk.doc_title)
        ).all()
    finally:
        SessionLocal.remove()

    return [{"document": r.doc_title, "chunks": r.chunks} for r in rows]

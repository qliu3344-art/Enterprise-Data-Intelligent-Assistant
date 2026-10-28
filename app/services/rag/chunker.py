"""Markdown 感知文本分块器 —— 条款级。

策略：
  1. 按 ## 二级标题切章，chunk 不跨章。
  2. 章内以 ### 三级标题（条款）为最小单位，一条一块。
  3. 每块正文开头带 ## 章标题和 ### 条标题，章标题同时进 metadata。
  4. 无 ### 的章整章一块；超长时按空行段落兜底切分，单段仍超长再按句末标点切。
  5. 条款本身不拆；单条超过 MAX_CHUNK_SIZE 只告警，留给后续按款/项二级拆分。

为什么是条款级：制度在业务里的引用单位是「第 X 条」，员工和审计都按条引用；
章只负责上下文。章级块会把 5~7 条不同主题平均进一个向量，问某一条时精度被稀释。
"""

import re
from dataclasses import dataclass, field
from typing import List

from app.logger import logger

# 单条条款超过这个长度只告警，不在这里拆；拆条会切出半句话。
#
# 为什么是 600 不是 800：embedding 模型 bge-small-zh-v1.5 的窗口是 512 token，
# 中文实测约 0.81 token/字，512 token ≈ 633 字。原来取 800 时，633~800 字之间的块
# 会被模型静默截断（向量里少一截）却触不到告警，等于没有保护。600 字 ≈ 485 token，
# 留出余量。改这个值不会自动重建索引（它不进 _doc_checksum），必须同时升 CHUNKER_VERSION。
MAX_CHUNK_SIZE = 600

# 分块口径变了就必须重建索引。vector_store._doc_checksum 会把它算进指纹，
# 否则源文件没变时 needs_reindex() 返回 False，旧索引会原样留着。
CHUNKER_VERSION = "clause-v2-20261028"

_CHAPTER_RE = re.compile(r"^##\s+(.+)")
_CLAUSE_RE = re.compile(r"^###\s+(.+)")
_CLAUSE_NO_RE = re.compile(r"^(第[一二三四五六七八九十百千\d]+条)")
_HORIZONTAL_RULE = "---"
# 句末标点（零宽 lookbehind，标点归前一句）或换行
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])|\n+")


@dataclass
class Chunk:
    """文本分块。"""
    text: str
    doc_title: str
    file_name: str
    chapter: str = ""        # 所属章（## 标题）
    section: str = ""        # 所属条（### 标题）；章前言为「」
    chunk_index: int = 0     # 在文档内的序号（只用于展示/排序）
    metadata: dict = field(default_factory=dict)
    chunk_key: str = ""      # 文档内稳定身份，增量重建按它对齐
    clause_no: str = ""      # 条款编号，如「第六条」
    parent_key: str = ""     # 父块（章）的稳定 key，后续父子块回填用


def _clean_body(raw: str) -> str:
    """清理条款/章正文：去掉首尾空行和章间分隔线，压缩连续空行。"""
    lines = [line.rstrip() for line in raw.split("\n")]
    while lines and (not lines[0].strip() or lines[0].strip() == _HORIZONTAL_RULE):
        lines.pop(0)
    while lines and (not lines[-1].strip() or lines[-1].strip() == _HORIZONTAL_RULE):
        lines.pop()
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _split_by_headings(text: str) -> list[dict]:
    """将文档按标题拆成「章 → 条」两级结构。

    Returns:
        [{"heading": "第二章 …", "preamble": "章首正文", "clauses": [
            {"heading": "第六条 …", "body": "条款正文"}, ...]}]
    """
    chapters: list[dict] = []
    chapter: dict | None = None
    clause: dict | None = None
    buf: list[str] = []

    def flush_clause() -> None:
        nonlocal clause, buf
        if clause is not None:
            clause["body"] = _clean_body("\n".join(buf))
            chapter["clauses"].append(clause)
            clause = None
        buf = []

    def flush_chapter() -> None:
        nonlocal chapter
        if chapter is None:
            return
        if clause is not None:
            flush_clause()
        else:
            chapter["preamble"] = _clean_body("\n".join(buf))
        chapters.append(chapter)
        chapter = None

    for line in text.split("\n"):
        h2_match = _CHAPTER_RE.match(line)
        h3_match = _CLAUSE_RE.match(line)

        if h2_match:
            flush_chapter()
            chapter = {
                "heading": h2_match.group(1).strip(),
                "preamble": "",
                "clauses": [],
            }
        elif h3_match:
            if chapter is None:
                # 兜底：文档没有 ##，只有 ###；当成一个无标题章。
                chapter = {"heading": "", "preamble": "", "clauses": []}
            elif clause is None and buf:
                # 章首、第一条之前的内容属于本章，不属于第一条。
                chapter["preamble"] = _clean_body("\n".join(buf))
                buf = []
            flush_clause()
            clause = {"heading": h3_match.group(1).strip(), "body": ""}
        else:
            buf.append(line)

    flush_chapter()
    return [c for c in chapters if c["heading"] or c["preamble"] or c["clauses"]]


def _parse_clause_no(heading: str) -> str:
    """从条标题里取编号：「第六条 迟到与早退」→「第六条」。"""
    match = _CLAUSE_NO_RE.match(heading)
    return match.group(1) if match else ""


def _split_sentences(para: str) -> list[str]:
    """按句末标点切句（标点归前一句）；没有标点的行按换行切。"""
    return [s for s in _SENTENCE_SPLIT_RE.split(para) if s and s.strip()]


def _split_long_paragraph(para: str, limit: int) -> list[str]:
    """单段超过 limit 时的兜底：按句末标点攒到接近 limit。

    单句本身超长就不再动——切句内会切出半个词，宁可整句保留。
    """
    parts: list[str] = []
    current = ""
    for sent in _split_sentences(para):
        candidate = sent if not current else current + sent
        if current and len(candidate) > limit:
            parts.append(current)
            current = sent
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _split_paragraphs(body: str, limit: int, warn_label: str = "") -> list[str]:
    """无条款的超长章兜底：先在空行段落边界切，单段仍超长再按句末标点切。

    两条路径都会告警——超长意味着这份文档本身缺结构，最好由维护者补 ### 小标题，
    而不是让分块器一直兜底。
    """
    parts: list[str] = []
    current = ""
    for para in re.split(r"\n\s*\n", body):
        if not para.strip():
            continue
        if len(para) > limit:
            if current:
                parts.append(current)
                current = ""
            subs = _split_long_paragraph(para, limit)
            if len(subs) == 1:
                logger.warning(
                    f"{warn_label} 单段 {len(para)} 字超过 {limit}，且无可切分的句边界；"
                    f"整段保留，建议给该章补 ### 小标题"
                )
            else:
                logger.warning(
                    f"{warn_label} 单段 {len(para)} 字超过 {limit}，已按句末标点拆成 {len(subs)} 块"
                )
            parts.extend(subs)
            continue
        candidate = para if not current else current + "\n\n" + para
        if current and len(candidate) > limit:
            parts.append(current)
            current = para
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def chunk_document(title: str, file_name: str, content: str, metadata: dict) -> List[Chunk]:
    """将一份完整文档拆成条款级 chunks。"""
    chapters = _split_by_headings(content)
    chunks: List[Chunk] = []
    used_keys: set[str] = set()

    def add_chunk(
        text: str,
        chapter_title: str,
        section: str,
        clause_no: str,
        parent_key: str,
        key_base: str,
    ) -> None:
        # 同一份文档里 key 必须唯一；标题重复时加 #2、#3 后缀。
        key = key_base
        suffix = 2
        while key in used_keys:
            key = f"{key_base}#{suffix}"
            suffix += 1
        used_keys.add(key)

        chunks.append(Chunk(
            text=text,
            doc_title=title,
            file_name=file_name,
            chapter=chapter_title,
            section=section,
            chunk_index=len(chunks),
            metadata=metadata,
            chunk_key=key,
            clause_no=clause_no,
            parent_key=parent_key,
        ))

    for chapter in chapters:
        chapter_title = chapter["heading"]
        parent_key = f"chapter:{chapter_title}" if chapter_title else "chapter:root"
        prefix = f"## {chapter_title}\n\n" if chapter_title else ""

        # 章首正文：有条款时它是前言块；没有条款时它整章就是一块。
        if chapter["preamble"]:
            if chapter["clauses"]:
                add_chunk(
                    text=prefix + chapter["preamble"],
                    chapter_title=chapter_title,
                    section="",
                    clause_no="",
                    parent_key=parent_key,
                    key_base=f"preamble:{chapter_title or 'root'}",
                )
            else:
                # 减去前缀长度，保证加上 ## 章标题后整块仍不超过 MAX_CHUNK_SIZE
                parts = _split_paragraphs(
                    chapter["preamble"],
                    MAX_CHUNK_SIZE - len(prefix),
                    warn_label=f"{file_name} / {chapter_title or '(无章)'}",
                )
                for i, part in enumerate(parts):
                    key = f"chapter:{chapter_title or 'root'}"
                    if len(parts) > 1:
                        key += f"#p{i}"
                    add_chunk(
                        text=prefix + part,
                        chapter_title=chapter_title,
                        section="",
                        clause_no="",
                        parent_key=parent_key,
                        key_base=key,
                    )

        for clause in chapter["clauses"]:
            heading = clause["heading"]
            body = clause["body"]
            if heading:
                text = f"{prefix}### {heading}\n\n{body}".rstrip()
                section = heading
                key_base = f"clause:{heading}"
            else:
                text = f"{prefix}{body}".rstrip()
                section = ""
                key_base = f"clause:{chapter_title or 'root'}#{len(chunks) + 1}"

            if len(text) > MAX_CHUNK_SIZE:
                logger.warning(
                    f"{file_name} / {heading or chapter_title} 长度 {len(text)} "
                    f"超过 {MAX_CHUNK_SIZE}；条款级分块不拆条，留给后续按款/项拆"
                )

            add_chunk(
                text=text,
                chapter_title=chapter_title,
                section=section,
                clause_no=_parse_clause_no(heading),
                parent_key=parent_key,
                key_base=key_base,
            )

    return chunks


def chunk_all(documents: list) -> List[Chunk]:
    """对所有文档执行分块。"""
    all_chunks: List[Chunk] = []
    for doc in documents:
        doc_chunks = chunk_document(
            title=doc.title,
            file_name=doc.file_name,
            content=doc.content,
            metadata=doc.metadata,
        )
        all_chunks.extend(doc_chunks)
        logger.info(f"  {doc.title}: {len(doc_chunks)} chunks")
    logger.info(f"总计 {len(all_chunks)} 个 chunks")
    return all_chunks

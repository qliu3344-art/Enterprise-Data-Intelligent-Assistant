"""Markdown 感知文本分块器。

策略：
  1. 按 ## 二级标题切分（对应制度的"章"）
  2. 每 chunk 最大 800 字符，超过则按 ### 三级标题继续切
  3. 相邻 chunk 之间保留最后 1 条款作为重叠（~100 字）
  4. 每个 chunk 携带完整的章节层级信息
"""

import re
from dataclasses import dataclass, field
from typing import List

from app.logger import logger

MAX_CHUNK_SIZE = 800  # 每块最大字符数
OVERLAP_CLAUSES = 1    # 相邻块重叠条款数

# 条款编号正则：数字编号（1. / 1）、中文序号（第一条 / 一、/（一））和 ### 标题
_CLAUSE_PATTERN = re.compile(
    r"^(\d+[\.\、\)]?\s)"               # 1. / 1) / 1、
    r"|^(第[一二三四五六七八九十百千\d]+[条款章节])"  # 第一条 / 第二章 / 第3条
    r"|^([（\(]?[一二三四五六七八九十]+[）\)]?\s)"  # （一）/ 一、
    r"|^(###\s)"                          # ### 三级标题
)


@dataclass
class Chunk:
    """文本分块。"""
    text: str
    doc_title: str
    file_name: str
    chapter: str = ""        # 所属章（## 标题）
    section: str = ""        # 所属节/条（### 标题）
    chunk_index: int = 0     # 在文档内的序号
    metadata: dict = field(default_factory=dict)


def _split_by_headings(text: str) -> list[dict]:
    """将文档按标题拆分为层级结构。

    返回 [{"level": 2, "heading": "第一章 总则", "body": "...", "subsections": [...]}]
    """
    lines = text.split("\n")
    sections: list[dict] = []
    current_section: dict | None = None
    current_body: list[str] = []

    for line in lines:
        h2_match = re.match(r"^##\s+(.+)", line)
        h3_match = re.match(r"^###\s+(.+)", line)

        if h2_match:
            if current_section is not None:
                current_section["body"] = "\n".join(current_body).strip()
            current_section = {
                "level": 2,
                "heading": h2_match.group(1).strip(),
                "body": "",
                "subsections": [],
            }
            sections.append(current_section)
            current_body = []
        elif h3_match and current_section is not None:
            # 将当前累积的 body 保存为上一节的结束，再开新小节
            if current_body:
                current_section["subsections"].append({
                    "level": 3,
                    "heading": "",
                    "body": "\n".join(current_body).strip(),
                })
                current_body = []
            current_section["subsections"].append({
                "level": 3,
                "heading": h3_match.group(1).strip(),
                "body": "",
            })
        else:
            current_body.append(line)

    # 保存最后一个 section
    if current_section is not None:
        if current_body:
            remaining = "\n".join(current_body).strip()
            if current_section["subsections"]:
                # 追加到最后一个 subsection
                last_sub = current_section["subsections"][-1]
                last_sub["body"] = (last_sub["body"] + "\n" + remaining).strip()
            else:
                current_section["body"] = remaining
        else:
            current_section["body"] = ""

    return sections


def chunk_document(title: str, file_name: str, content: str, metadata: dict) -> List[Chunk]:
    """将一份完整文档拆分为多个 Chunk。"""
    sections = _split_by_headings(content)
    chunks: List[Chunk] = []
    chunk_idx = 0

    for sec in sections:
        chapter_title = sec["heading"]

        if sec["subsections"]:
            # 有三级标题：按小节切分
            sub_chunks: list[str] = []
            current_text = f"## {chapter_title}\n\n"

            for sub in sec["subsections"]:
                heading_line = f"### {sub['heading']}\n" if sub["heading"] else ""
                candidate = heading_line + sub["body"]

                if len(current_text) + len(candidate) > MAX_CHUNK_SIZE and current_text.strip():
                    sub_chunks.append(current_text.strip())
                    current_text = f"## {chapter_title}\n\n{candidate}"
                else:
                    current_text += "\n\n" + candidate

            if current_text.strip():
                sub_chunks.append(current_text.strip())

            # 生成 chunks，相邻之间携带重叠条款
            for i, text in enumerate(sub_chunks):
                if i > 0 and OVERLAP_CLAUSES > 0:
                    # 从前一个 chunk 尾部取最后几条条款作为重叠
                    prev_lines = sub_chunks[i - 1].split("\n")
                    overlap_lines = []
                    count = 0
                    for line in reversed(prev_lines):
                        if _CLAUSE_PATTERN.match(line.strip()):
                            overlap_lines.insert(0, line)
                            count += 1
                            if count >= OVERLAP_CLAUSES:
                                break
                    if overlap_lines:
                        text = "\n".join(overlap_lines) + "\n\n" + text

                chunks.append(Chunk(
                    text=text,
                    doc_title=title,
                    file_name=file_name,
                    chapter=chapter_title,
                    section=sub.get("heading", ""),
                    chunk_index=chunk_idx,
                    metadata=metadata,
                ))
                chunk_idx += 1
        else:
            # 无三级标题：整章作为一个 chunk
            text = f"## {chapter_title}\n\n{sec['body']}"
            if len(text) > MAX_CHUNK_SIZE * 2:
                # 超长章节：按段落切分
                paragraphs = sec["body"].split("\n\n")
                current = f"## {chapter_title}\n\n"
                for para in paragraphs:
                    if len(current) + len(para) > MAX_CHUNK_SIZE:
                        chunks.append(Chunk(
                            text=current.strip(),
                            doc_title=title,
                            file_name=file_name,
                            chapter=chapter_title,
                            section="",
                            chunk_index=chunk_idx,
                            metadata=metadata,
                        ))
                        chunk_idx += 1
                        current = f"## {chapter_title}（续）\n\n{para}"
                    else:
                        current += "\n\n" + para
                if current.strip():
                    chunks.append(Chunk(
                        text=current.strip(),
                        doc_title=title,
                        file_name=file_name,
                        chapter=chapter_title,
                        section="",
                        chunk_index=chunk_idx,
                        metadata=metadata,
                    ))
                    chunk_idx += 1
            else:
                chunks.append(Chunk(
                    text=text,
                    doc_title=title,
                    file_name=file_name,
                    chapter=chapter_title,
                    section="",
                    chunk_index=chunk_idx,
                    metadata=metadata,
                ))
                chunk_idx += 1

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

"""文档加载器：扫描 data/documents/ 目录，解析 Markdown 文件及其元数据。"""

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

from app.logger import logger

# 项目根目录（app/services/rag/ → app/services/ → app/ → 项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DOCUMENTS_DIR = PROJECT_ROOT / "data" / "documents"


@dataclass
class Document:
    """RAG 文档数据结构。"""
    file_name: str
    title: str
    content: str
    metadata: dict = field(default_factory=dict)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 Markdown 顶部的表格元数据。

    格式：
    | 属性 | 内容 |
    |------|------|
    | 文档编号 | HR-2024-001 |

    返回 (metadata_dict, body_content)
    """
    lines = text.strip().split("\n")
    meta: dict = {}
    body_start = 0

    in_table = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and "属性" in stripped and "内容" in stripped:
            in_table = True
        elif in_table and stripped.startswith("|---"):
            continue  # 分隔行
        elif in_table and stripped.startswith("|"):
            parts = [p.strip() for p in stripped.split("|")[1:-1]]
            if len(parts) >= 2 and parts[0] and parts[1]:
                meta[parts[0]] = parts[1]
        elif in_table and stripped == "":
            body_start = i + 1
            break
        elif in_table and not stripped.startswith("|"):
            body_start = i
            break
        else:
            body_start = i + 1 if not in_table else body_start

    return meta, "\n".join(lines[body_start:]).strip()


def _extract_title(text: str) -> str:
    """从 Markdown 内容中提取一级标题作为文档标题。"""
    match = re.match(r"^#\s+(.+)", text.strip())
    return match.group(1).strip() if match else "未知文档"


# frontmatter 里的「生效日期」形如「2024年1月1日」，另兼容 ISO 的「2024-01-01」
_CN_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def parse_effective_date(raw) -> Optional[date]:
    """把 frontmatter 的「生效日期」解析成真正的 date。

    Args:
        raw: 原始值，如「2024年1月1日」

    Returns:
        解析成功返回 date；解析失败或为空返回 None。

    ⚠️ None 的语义是「长期有效」，不是「未生效」。所以检索侧的过滤条件必须
    写成 `effective_date IS NULL OR effective_date <= CURRENT_DATE` —— 写成
    裸的 `<= CURRENT_DATE` 会把解析失败的制度静默排除出检索，表现是
    「制度上线了但答不出来」，没有任何一处报错。
    """
    if not raw:
        return None
    text = str(raw).strip()
    for pattern in (_CN_DATE, _ISO_DATE):
        m = pattern.search(text)
        if not m:
            continue
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            logger.warning(f"生效日期非法，按长期有效处理: {raw!r}")
            return None
    logger.warning(f"生效日期无法解析，按长期有效处理: {raw!r}")
    return None


def load_all_documents(docs_dir: Optional[str] = None) -> List[Document]:
    """加载指定目录下所有 .md 文件（跳过 INDEX.md）。"""
    directory = Path(docs_dir) if docs_dir else DOCUMENTS_DIR
    documents: List[Document] = []

    if not directory.exists():
        logger.warning(f"文档目录不存在: {directory}")
        return documents

    for md_file in sorted(directory.glob("*.md")):
        if md_file.name == "INDEX.md":
            continue

        try:
            raw = md_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"读取文件失败 {md_file.name}: {e}")
            continue

        # 先从全文提取标题（在 frontmatter 之前）
        title = _extract_title(raw)

        meta, body = _parse_frontmatter(raw)

        # 如果正文中没有标题，用 meta 中的文档名称或文件名
        if not title:
            title = meta.get("文档名称", md_file.stem)

        # 提取关联的 data_type
        data_type = meta.get("关联数据", meta.get("数据关联", ""))

        doc = Document(
            file_name=md_file.name,
            title=title,
            content=body,
            metadata={
                **meta,
                "file_name": md_file.name,
                "data_type": data_type,
            },
        )
        documents.append(doc)
        logger.info(f"加载文档: {title} ({md_file.name})")

    logger.info(f"共加载 {len(documents)} 份文档")
    return documents

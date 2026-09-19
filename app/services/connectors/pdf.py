"""PDF 连接器：pdfplumber 提取文本 + LLM 结构化。"""

import json
import os

import pandas as pd
import pdfplumber
from dashscope import Generation

from app.config import settings
from app.exceptions import LLMException
from app.logger import logger
from .base import BaseConnector

PDF_EXTRACT_PROMPT = """你是一个专业的PDF数据提取助手。以下是从PDF报表中提取的文本内容，请将其转换为结构化的表格数据。

## PDF文本内容
{pdf_text}

## 要求
1. 识别表格数据的列名和行数据
2. 如果文本中包含表格，提取所有行和列
3. 如果文本是段落形式（非表格），提取关键指标和数值
4. 输出格式：{{"headers": ["列1", "列2", ...], "rows": [["值1", "值2", ...], ...]}}
5. 只输出JSON，不要其他内容
"""


class PDFConnector(BaseConnector):
    def connect(self) -> bool:
        file_path = self.config.get("file_path", "")
        return bool(file_path and os.path.exists(file_path))

    def read(self) -> pd.DataFrame:
        file_path = self.config["file_path"]

        # 步骤1：pdfplumber 提取文本
        text = self._extract_text(file_path)
        if not text.strip():
            raise ValueError(f"PDF 未能提取到文本内容: {file_path}")

        # 步骤2：LLM 结构化提取
        extracted = self._llm_extract(text)

        # 步骤3：转换为 DataFrame
        if not extracted.get("rows"):
            raise ValueError("LLM 未能从 PDF 提取到表格数据")

        return pd.DataFrame(extracted["rows"], columns=extracted["headers"])

    def validate(self, df: pd.DataFrame) -> tuple[bool, str]:
        if df.empty:
            return False, "PDF 未能提取到表格数据"
        return True, ""

    def _extract_text(self, file_path: str) -> str:
        """使用 pdfplumber 提取每页文本。"""
        pages_text = []
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                t = page.extract_text()
                if t:
                    pages_text.append(t)
        text = "\n\n".join(pages_text)
        logger.info(f"PDF 文本提取完成: {len(text)} 字符, {len(pages_text)} 页")
        return text

    def _llm_extract(self, text: str) -> dict:
        """调用 LLM 将非结构化文本转为结构化 JSON。

        超过单次处理的文本按页分批送 LLM，最后合并所有提取结果。
        """
        MAX_CHUNK_CHARS = 6000  # 单次 LLM 调用的文本上限（留 buffer 给 prompt）

        # 按页切分（_extract_text 中每页用 \n\n 拼接）
        pages = text.split("\n\n")
        chunks: list[str] = []
        current = ""
        for page in pages:
            if len(current) + len(page) > MAX_CHUNK_CHARS and current:
                chunks.append(current)
                current = page
            else:
                current = current + "\n\n" + page if current else page
        if current:
            chunks.append(current)

        if len(chunks) > 1:
            logger.warning(
                f"PDF 文本过长（{len(text)} 字符），分 {len(chunks)} 批处理"
            )

        all_headers: list[str] = []
        all_rows: list[list] = []

        for chunk_idx, chunk_text in enumerate(chunks):
            try:
                resp = Generation.call(
                    model=settings.LLM_MODEL,
                    messages=[
                        {
                            "role": "user",
                            "content": PDF_EXTRACT_PROMPT.format(pdf_text=chunk_text),
                        }
                    ],
                    result_format="message",
                    temperature=0.1,
                )

                if resp.status_code != 200:
                    raise LLMException(f"LLM 调用失败: {resp.message}")

                content = resp.output.choices[0].message.content.strip()

                # 清理 markdown 代码块包裹
                if content.startswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                chunk_result = json.loads(content)

                chunk_headers = chunk_result.get("headers", [])
                chunk_rows = chunk_result.get("rows", [])

                # 合并：第一批的 headers 作为基准，后续批只取 rows
                if chunk_idx == 0:
                    all_headers = chunk_headers
                elif chunk_headers and chunk_headers != all_headers:
                    # 列数不同时 pandas 会报错，但列数相同、语义不同会「静默串列」——
                    # 数字对错行且无人察觉，比直接失败危险得多。
                    # 本连接器的契约是「一个 PDF 一张表」，遇到不同结构就明确报错，不猜不合并。
                    raise LLMException(
                        f"PDF 第 {chunk_idx + 1} 批的列结构与首批不一致："
                        f"首批 {all_headers}，本批 {chunk_headers}。"
                        f"该连接器一次只处理单表 PDF，请拆分后重试。"
                    )
                all_rows.extend(chunk_rows)

            except json.JSONDecodeError as e:
                logger.error(f"LLM 返回非 JSON: {content[:200]}")
                raise LLMException(f"LLM 返回格式异常，无法解析为 JSON: {e}")
            except Exception as e:
                if isinstance(e, LLMException):
                    raise
                raise LLMException(f"LLM 提取失败: {e}")

        return {"headers": all_headers, "rows": all_rows}

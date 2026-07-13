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
        """调用 LLM 将非结构化文本转为结构化 JSON。"""
        # 截断过长文本（dashscope 有 token 限制）
        truncated = text[:8000] if len(text) > 8000 else text

        try:
            resp = Generation.call(
                model=settings.LLM_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": PDF_EXTRACT_PROMPT.format(pdf_text=truncated),
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

            return json.loads(content)

        except json.JSONDecodeError as e:
            logger.error(f"LLM 返回非 JSON: {content[:200]}")
            raise LLMException(f"LLM 返回格式异常，无法解析为 JSON: {e}")
        except Exception as e:
            if isinstance(e, LLMException):
                raise
            raise LLMException(f"LLM 提取失败: {e}")

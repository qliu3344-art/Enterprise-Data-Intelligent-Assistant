"""Excel 连接器：读取 .xlsx / .xls 文件。"""

import os

import pandas as pd

from .base import BaseConnector


class ExcelConnector(BaseConnector):
    def connect(self) -> bool:
        file_path = self.config.get("file_path", "")
        return bool(file_path and os.path.exists(file_path))

    def read(self) -> pd.DataFrame:
        # sheet_name 为 None 或空时默认取第一个 sheet
        sheet = self.config.get("sheet_name") or 0
        skip = self.config.get("skip_rows") or 0
        return pd.read_excel(
            self.config["file_path"],
            sheet_name=sheet,
            skiprows=skip,
        )

    def validate(self, df: pd.DataFrame) -> tuple[bool, str]:
        if df.empty:
            return False, "Excel 文件为空"
        if len(df.columns) < 2:
            return False, f"列数过少({len(df.columns)})，可能解析有误"
        return True, ""

"""CSV 连接器：自动检测编码和分隔符。"""

import os

import pandas as pd

from .base import BaseConnector


class CSVConnector(BaseConnector):
    # 常见中文编码，按优先级尝试
    _ENCODINGS = ("utf-8", "gbk", "gb2312", "latin-1")

    def connect(self) -> bool:
        file_path = self.config.get("file_path", "")
        return bool(file_path and os.path.exists(file_path))

    def read(self) -> pd.DataFrame:
        file_path = self.config["file_path"]
        delimiter = self.config.get("delimiter") or ","
        skip = self.config.get("skip_rows") or 0

        # 自动检测编码
        for enc in self._ENCODINGS:
            try:
                return pd.read_csv(
                    file_path,
                    delimiter=delimiter,
                    encoding=enc,
                    skiprows=skip,
                )
            except (UnicodeDecodeError, UnicodeError):
                continue

        raise ValueError(f"无法识别文件编码: {file_path}")

    def validate(self, df: pd.DataFrame) -> tuple[bool, str]:
        if df.empty:
            return False, "CSV 文件为空"
        if len(df.columns) < 2:
            return False, f"列数过少({len(df.columns)})，请检查分隔符设置"
        return True, ""

"""多源数据连接器抽象基类。"""

from abc import ABC, abstractmethod

import pandas as pd


class BaseConnector(ABC):
    """所有数据源连接器的抽象基类。

    每种数据源类型（Excel/CSV/MySQL/PDF）实现自己的 Connector 子类，
    对外暴露统一接口：connect → read → validate → get_headers。
    """

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def connect(self) -> bool:
        """建立连接或验证文件存在，返回是否成功。"""
        ...

    @abstractmethod
    def read(self) -> pd.DataFrame:
        """读取数据，返回 DataFrame。"""
        ...

    @abstractmethod
    def validate(self, df: pd.DataFrame) -> tuple[bool, str]:
        """验证数据基本完整性。

        Returns:
            (是否通过, 错误信息)
        """
        ...

    def get_headers(self, df: pd.DataFrame) -> list[str]:
        """获取表头列表（供 aligner 语义对齐使用）。"""
        return list(df.columns)

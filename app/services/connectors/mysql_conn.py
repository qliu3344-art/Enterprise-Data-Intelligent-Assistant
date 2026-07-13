"""MySQL 连接器：读取远程数据库表或查询结果。"""

import pandas as pd
from sqlalchemy import create_engine, text

from .base import BaseConnector


class MySQLConnector(BaseConnector):
    def connect(self) -> bool:
        try:
            engine = self._build_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine.dispose()
            return True
        except Exception:
            return False

    def read(self) -> pd.DataFrame:
        engine = self._build_engine()
        try:
            query = self.config.get("db_query", "")
            return pd.read_sql(query, engine)
        finally:
            engine.dispose()

    def validate(self, df: pd.DataFrame) -> tuple[bool, str]:
        if df.empty:
            return False, "查询结果为空"
        return True, ""

    def _build_engine(self):
        host = self.config["db_host"]
        port = self.config.get("db_port", 3306)
        user = self.config["db_user"]
        password = self.config.get("db_password", "")
        db_name = self.config["db_name"]
        url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}?charset=utf8mb4"
        return create_engine(url)

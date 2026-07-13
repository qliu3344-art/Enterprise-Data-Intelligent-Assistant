"""Connector 工厂函数。

根据数据源类型返回对应的连接器实例。
"""

from app.models.datasource import DataSource
from app.exceptions import ConnectorException


def get_connector(source: DataSource):
    """根据数据源配置返回对应的连接器实例。

    Args:
        source: DataSource ORM 对象

    Returns:
        BaseConnector 子类实例

    Raises:
        ConnectorException: 不支持的数据源类型
    """
    source_type = source.source_type.lower()

    if source_type == "excel":
        from app.services.connectors.excel import ExcelConnector
        return ExcelConnector(_source_to_config(source))
    elif source_type == "csv":
        from app.services.connectors.csv_conn import CSVConnector
        return CSVConnector(_source_to_config(source))
    elif source_type == "mysql":
        from app.services.connectors.mysql_conn import MySQLConnector
        return MySQLConnector(_source_to_config(source))
    elif source_type == "pdf":
        from app.services.connectors.pdf import PDFConnector
        return PDFConnector(_source_to_config(source))
    else:
        raise ConnectorException(f"不支持的数据源类型: {source_type}")


def _source_to_config(source: DataSource) -> dict:
    """将 ORM 对象转为连接器所需的配置字典。"""
    return {
        "source_id": source.id,
        "source_name": source.name,
        "source_type": source.source_type,
        "file_path": source.file_path,
        "db_host": source.db_host,
        "db_port": source.db_port or 3306,
        "db_name": source.db_name,
        "db_user": source.db_user,
        "db_password": source.db_password,
        "db_query": source.db_query,
        "sheet_name": source.sheet_name,
        "delimiter": source.delimiter or ",",
        "encoding": source.encoding or "utf-8",
        "skip_rows": source.skip_rows or 0,
    }

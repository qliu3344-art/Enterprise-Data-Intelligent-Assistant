"""采集编排服务：协调 Connector 读取 + 原始数据入库。"""

from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.logger import logger
from app.models.datasource import DataSource
from app.models.raw_record import RawRecord
from app.services.connectors import get_connector


def collect_from_source(source: DataSource, db: Session) -> dict:
    """从单个数据源采集数据，返回采集结果摘要。

    Args:
        source: DataSource ORM 对象
        db: 数据库会话

    Returns:
        {"batch_id": str, "source_id": int, "source_name": str,
         "record_count": int, "status": str, "message": str}

    Raises:
        Exception: 连接或读取失败时抛出
    """
    batch_id = (
        f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{source.id}"
    )
    logger.info(f"开始采集: source={source.name}, batch={batch_id}")

    # 1. 获取连接器
    connector = get_connector(source)

    # 2. 连接
    if not connector.connect():
        msg = f"无法连接数据源: {source.name}"
        _mark_source_error(source, msg, db)
        raise ConnectionError(msg)

    # 3. 读取
    df = connector.read()

    # 4. 验证
    ok, msg = connector.validate(df)
    if not ok:
        _mark_source_error(source, msg, db)
        raise ValueError(f"数据验证失败: {msg}")

    # 5. 批量写入 raw_records 表（处理 NaN 值）
    records = []
    for idx, row in df.iterrows():
        # 将 NaN/NaT 替换为 None，确保 JSON 序列化兼容
        clean_row = {}
        for k, v in row.to_dict().items():
            if pd.isna(v):
                clean_row[k] = None
            else:
                clean_row[k] = v
        records.append(
            RawRecord(
                source_id=source.id,
                batch_id=batch_id,
                raw_data=clean_row,
                source_row_index=int(idx) if hasattr(idx, "__int__") else None,
                status="raw",
            )
        )

    db.add_all(records)

    # 6. 更新数据源状态
    source.last_collect_at = datetime.now()
    source.last_collect_count = len(df)
    source.error_message = None
    source.status = "active"

    db.commit()

    logger.info(
        f"采集完成: source={source.name}, batch={batch_id}, "
        f"records={len(df)}"
    )

    return {
        "batch_id": batch_id,
        "source_id": source.id,
        "source_name": source.name,
        "record_count": len(df),
        "status": "success",
        "message": "",
    }


def collect_batch(sources: list[DataSource], db: Session) -> dict:
    """批量采集多个数据源。每个数据源使用独立事务。

    Returns:
        {"total_sources": int, "success_count": int, "failed_count": int,
         "results": list[dict]}
    """
    results = []
    success_count = 0
    failed_count = 0

    for source in sources:
        try:
            result = collect_from_source(source, db)
            results.append(result)
            success_count += 1
        except Exception as e:
            # 失败时回滚当前事务，避免影响后续源
            db.rollback()
            results.append(
                {
                    "batch_id": "",
                    "source_id": source.id,
                    "source_name": source.name,
                    "record_count": 0,
                    "status": "failed",
                    "message": str(e),
                }
            )
            failed_count += 1

    return {
        "total_sources": len(sources),
        "success_count": success_count,
        "failed_count": failed_count,
        "results": results,
    }


def _mark_source_error(source: DataSource, message: str, db: Session):
    """标记数据源为错误状态。"""
    source.status = "error"
    source.error_message = message
    db.commit()

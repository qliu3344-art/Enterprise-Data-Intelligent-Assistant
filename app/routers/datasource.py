"""数据源管理 API。"""

import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.exceptions import NotFoundException, ValidationException
from app.logger import logger
from app.models.datasource import DataSource
from app.schemas.datasource import (
    DataSourceCreate,
    DataSourceUpdate,
    DataSourceResponse,
    DataSourceListResponse,
    DataSourceTestResponse,
)
from app.services.connectors import get_connector
from app.services.aligner import align_headers
from app.models.schema_mapping import SchemaMapping

router = APIRouter(prefix="/datasources", tags=["数据源管理"])


@router.post("", response_model=dict)
def create_datasource(body: DataSourceCreate, db: Session = Depends(get_db)):
    """注册新的数据源。"""
    source = DataSource(**body.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return {"code": 200, "message": "ok", "data": _to_response(source)}


@router.get("", response_model=dict)
def list_datasources(
    page: int = 1,
    page_size: int = 20,
    source_type: str = "",
    db: Session = Depends(get_db),
):
    """数据源列表（分页）。"""
    query = db.query(DataSource)
    if source_type:
        query = query.filter(DataSource.source_type == source_type)
    total = query.count()
    items = (
        query.order_by(DataSource.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "items": [_to_response(item) for item in items],
            "total": total,
        },
    }


@router.get("/{source_id}", response_model=dict)
def get_datasource(source_id: int, db: Session = Depends(get_db)):
    """数据源详情。"""
    source = _get_or_404(source_id, db)
    return {"code": 200, "message": "ok", "data": _to_response(source)}


@router.put("/{source_id}", response_model=dict)
def update_datasource(
    source_id: int, body: DataSourceUpdate, db: Session = Depends(get_db)
):
    """更新数据源配置。"""
    source = _get_or_404(source_id, db)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(source, key, value)
    source.updated_at = datetime.now()
    db.commit()
    db.refresh(source)
    return {"code": 200, "message": "ok", "data": _to_response(source)}


@router.delete("/{source_id}", response_model=dict)
def delete_datasource(source_id: int, db: Session = Depends(get_db)):
    """删除数据源。"""
    source = _get_or_404(source_id, db)
    db.delete(source)
    db.commit()
    return {"code": 200, "message": "数据源已删除", "data": None}


@router.post("/{source_id}/test", response_model=dict)
def test_datasource_connection(source_id: int, db: Session = Depends(get_db)):
    """测试数据源连接。"""
    source = _get_or_404(source_id, db)
    try:
        connector = get_connector(source)
        if not connector.connect():
            return {
                "code": 200,
                "message": "ok",
                "data": {
                    "success": False,
                    "message": "连接失败：文件不存在或数据库不可达",
                    "row_count": None,
                    "columns": None,
                },
            }
        df = connector.read()
        ok, msg = connector.validate(df)
        return {
            "code": 200,
            "message": "ok",
            "data": {
                "success": ok,
                "message": msg if not ok else "连接成功",
                "row_count": len(df),
                "columns": list(df.columns),
            },
        }
    except Exception as e:
        return {
            "code": 200,
            "message": "ok",
            "data": {
                "success": False,
                "message": str(e),
                "row_count": None,
                "columns": None,
            },
        }


@router.post("/{source_id}/align", response_model=dict)
def align_datasource_headers(source_id: int, db: Session = Depends(get_db)):
    """LLM 驱动的表头语义对齐：将数据源的原始列名映射到标准字段。"""
    source = _get_or_404(source_id, db)

    try:
        connector = get_connector(source)
        if not connector.connect():
            return {
                "code": 200,
                "message": "ok",
                "data": {
                    "success": False,
                    "message": "无法连接数据源",
                    "mapping": {},
                    "unmapped": [],
                    "confidence": 0,
                },
            }
        df = connector.read()
        headers = list(df.columns)

        # 推断数据类型提示
        data_type_hint = ""
        name_lower = source.name.lower()
        if "考勤" in name_lower or "attendance" in name_lower:
            data_type_hint = "attendance"
        elif "销售" in name_lower or "sales" in name_lower:
            data_type_hint = "sales"
        elif "客户" in name_lower or "customer" in name_lower:
            data_type_hint = "customer"
        elif "运营" in name_lower or "operation" in name_lower:
            data_type_hint = "operation"

        result = align_headers(headers, data_type_hint)

        # 保存映射结果到 schema_mappings 表
        mapping_record = SchemaMapping(
            source_id=source.id,
            mapping_data=result["mapping"],
            unmapped_columns=result["unmapped"],
            confidence=result["confidence"],
            manual_reviewed=False,
        )
        db.add(mapping_record)
        db.commit()

        return {
            "code": 200,
            "message": "ok",
            "data": {
                "success": True,
                "source_id": source.id,
                "headers": headers,
                "mapping": result["mapping"],
                "unmapped": result["unmapped"],
                "confidence": result["confidence"],
                "mapping_id": mapping_record.id,
            },
        }
    except Exception as e:
        logger.error(f"表头对齐失败: {e}")
        return {
            "code": 200,
            "message": "ok",
            "data": {
                "success": False,
                "message": str(e),
                "mapping": {},
                "unmapped": [],
                "confidence": 0,
            },
        }


@router.post("/upload", response_model=dict)
async def upload_datasource_file(
    file: UploadFile = File(...),
):
    """上传数据文件（Excel/CSV/PDF），返回文件路径供创建数据源时使用。"""
    # 验证文件类型
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xlsx", ".xls", ".csv", ".pdf", ".txt"):
        raise ValidationException(f"不支持的文件格式: {ext}")

    # 生成唯一文件名
    safe_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, safe_name)

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    logger.info(f"文件上传成功: {file.filename} → {file_path}")

    # 根据扩展名推断 source_type
    type_map = {".xlsx": "excel", ".xls": "excel", ".csv": "csv", ".pdf": "pdf", ".txt": "csv"}
    inferred_type = type_map.get(ext, "excel")

    return {
        "code": 200,
        "message": "ok",
        "data": {
            "file_path": file_path,
            "file_name": file.filename,
            "file_size": len(content),
            "inferred_type": inferred_type,
        },
    }


def _get_or_404(source_id: int, db: Session) -> DataSource:
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise NotFoundException(f"数据源不存在: id={source_id}")
    return source


def _to_response(source: DataSource) -> dict:
    """ORM 对象 → 响应字典。"""
    return {
        "id": source.id,
        "name": source.name,
        "source_type": source.source_type,
        "file_path": source.file_path,
        "db_host": source.db_host,
        "db_port": source.db_port,
        "db_name": source.db_name,
        "db_user": source.db_user,
        "db_query": source.db_query,
        "sheet_name": source.sheet_name,
        "delimiter": source.delimiter,
        "encoding": source.encoding,
        "skip_rows": source.skip_rows,
        "status": source.status,
        "last_collect_at": source.last_collect_at.isoformat() if source.last_collect_at else None,
        "last_collect_count": source.last_collect_count,
        "error_message": source.error_message,
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "updated_at": source.updated_at.isoformat() if source.updated_at else None,
    }

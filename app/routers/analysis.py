"""业务分析 API — 汇总统计、趋势分析、质量报告、数据导出。"""

import os

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.analyzer import (
    get_summary,
    get_trend,
    get_quality_report,
    get_dashboard,
    export_to_excel,
)
from app.schemas.analysis import ExportRequest

router = APIRouter(prefix="/analysis", tags=["业务分析"])


@router.get("/dashboard", response_model=dict)
def dashboard(db: Session = Depends(get_db)):
    """仪表盘：一次请求返回 summary + trend + quality，避免并发。"""
    data = get_dashboard(db)
    return {"code": 200, "message": "ok", "data": data}


@router.get("/summary", response_model=dict)
def summary(db: Session = Depends(get_db)):
    """汇总统计：按 data_type 分组的总量、异常率、质量分。"""
    data = get_summary(db)
    return {"code": 200, "message": "ok", "data": data}


@router.get("/trend", response_model=dict)
def trend(
    metric: str = "record_count",
    data_type: str = "",
    granularity: str = "month",
    db: Session = Depends(get_db),
):
    """趋势分析：按时间粒度聚合。

    - metric: "record_count" | "anomaly_rate"
    - data_type: 为空则全部
    - granularity: "day" | "month"
    """
    data = get_trend(
        db, metric=metric, data_type=data_type, granularity=granularity
    )
    return {"code": 200, "message": "ok", "data": data}


@router.get("/quality", response_model=dict)
def quality(db: Session = Depends(get_db)):
    """数据质量报告：缺失率、异常率、按部门质量分。"""
    data = get_quality_report(db)
    return {"code": 200, "message": "ok", "data": data}


@router.post("/export", response_model=dict)
def export_data(body: ExportRequest, db: Session = Depends(get_db)):
    """导出清洗后数据为 Excel 文件，返回下载链接。"""
    filepath = export_to_excel(
        db,
        data_types=body.data_types,
        date_start=body.date_start,
        date_end=body.date_end,
        include_anomalies=body.include_anomalies,
    )
    filename = os.path.basename(filepath)
    return {
        "code": 200,
        "message": "导出成功",
        "data": {
            "filename": filename,
            "download_url": f"/api/v1/analysis/download/{filename}",
        },
    }


@router.get("/download/{filename}")
def download_file(filename: str):
    """下载导出的 Excel 文件。"""
    from app.config import settings

    filepath = os.path.join(settings.EXPORT_DIR, filename)
    if not os.path.exists(filepath):
        from app.exceptions import NotFoundException

        raise NotFoundException("文件不存在或已过期")
    return FileResponse(
        filepath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )

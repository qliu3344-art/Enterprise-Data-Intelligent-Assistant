"""自动化客户数据处理平台 — FastAPI 应用入口。"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

from app.config import settings
from app.exceptions import AppException
from app.logger import logger

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="多源数据采集、清洗、标准化与分析平台",
)

# CORS（开发时允许前端跨域）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# —— 全局异常处理 ——
@app.exception_handler(AppException)
def handle_app_exception(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": exc.message, "data": None},
    )


@app.exception_handler(Exception)
def handle_unexpected_exception(request: Request, exc: Exception):
    logger.exception(f"未处理异常: {exc}")
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误", "data": None},
    )


# —— 健康检查 ——
@app.get("/health")
def health_check():
    return {"status": "ok", "version": settings.VERSION}


@app.on_event("startup")
def on_startup():
    logger.info(f"{settings.PROJECT_NAME} v{settings.VERSION} 启动成功")

    # 自动初始化 RAG 索引（如果文档有更新）
    try:
        from app.services.rag.vector_store import index_all, needs_reindex
        if needs_reindex():
            logger.info("检测到文档变更，自动重建 RAG 索引...")
            result = index_all(force=True)
            logger.info(f"RAG 索引完成: {result['documents']} 文档 → {result['chunks']} chunks")
        else:
            logger.info("RAG 索引已是最新，跳过重建")
    except Exception as e:
        logger.warning(f"RAG 索引初始化跳过（可能首次启动）: {e}")


# —— 路由注册 ——
from app.routers import datasource, collect, clean, analysis, query, rag, feedback

app.include_router(datasource.router, prefix="/api/v1")
app.include_router(collect.router, prefix="/api/v1")
app.include_router(clean.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")
app.include_router(rag.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")

# 生产环境挂载前端 dist（SPA fallback 模式）
import os
from fastapi.responses import FileResponse

_frontend_dist = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "frontend", "dist"
)
_index_html = os.path.join(_frontend_dist, "index.html")

if os.path.exists(_frontend_dist):
    # SPA fallback: 所有未匹配的非 API GET 请求
    #   1. 若路径对应 dist 中的实际文件 → 直接返回
    #   2. 否则返回 index.html → Vue Router 接管前端路由
    _favicon = os.path.join(_frontend_dist, "favicon.ico")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        if not full_path:
            return FileResponse(_index_html)
        file_path = os.path.join(_frontend_dist, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(_index_html)

    @app.get("/favicon.ico", include_in_schema=False)
    async def _favicon():
        if os.path.exists(_favicon):
            return FileResponse(_favicon)
        return JSONResponse(status_code=404, content={})

    logger.info(f"前端静态文件已挂载: {_frontend_dist} (SPA fallback 模式)")



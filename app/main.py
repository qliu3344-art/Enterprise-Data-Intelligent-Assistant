"""企业数据智能助手 — FastAPI 应用入口。"""

import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

from app.config import settings
from app.exceptions import AppException
from app.logger import logger


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """uvicorn 的事件循环工厂：Windows 上强制用 SelectorEventLoop。

    会话检查点走 psycopg 的异步模式，而 psycopg 明确不支持 Windows 默认的
    ProactorEventLoop。uvicorn 0.49 在 win32 上是**显式**返回 ProactorEventLoop 的
    （见 uvicorn.loops.asyncio.asyncio_loop_factory），并且是直接传 loop_factory 给
    asyncio.run，绕过全局事件循环策略——所以 asyncio.set_event_loop_policy() 在这里
    不管用，只能从启动参数换掉工厂：

        python -m uvicorn app.main:app --loop app.main:selector_loop_factory

    代价是 SelectorEventLoop 在 Windows 上不支持 asyncio 子进程。本项目没有用到
    （MCP 是服务端、PDF 解析在进程内），所以这里安全；将来要是引入了 stdio 子进程
    客户端，这条要重新评估。
    """
    return asyncio.SelectorEventLoop()


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
async def on_startup():
    logger.info(f"{settings.PROJECT_NAME} v{settings.VERSION} 启动成功")

    # L0 参数防线：意图标签单 token 校验，fail fast。
    # 多 token 标签不会报错，只会安静地返回一个偏高的错误概率——静默错误。
    # 所以这里不 try/except：宁可服务起不来，也不能带着错的置信度上线。
    from app.services.intent_router import verify_single_token_labels

    lengths = verify_single_token_labels()
    logger.info(f"意图标签单 token 校验通过: {lengths}")

    # 自动初始化 RAG 索引（per-document 增量，只重算变化的文档）
    try:
        from app.services.rag.vector_store import index_all
        result = index_all()
        if result["reindexed"]:
            logger.info(f"RAG 索引更新完成: {result['documents']} 文档 → {result['chunks']} chunks")
        else:
            logger.info("RAG 索引已是最新，跳过重建")
    except Exception as e:
        logger.warning(f"RAG 索引初始化跳过（可能首次启动）: {e}")

    # 预热 Agent：会话检查点连不上要在这里就炸出来，而不是等第一个用户提问。
    # 和上面那条「意图标签单 token 校验」同理——静默降级的代价是「服务看着正常、
    # 记忆其实没落盘」，那种故障没有告警、没有错误日志，只有事后对不上账。
    from app.services.query_agent import get_agent

    await get_agent()


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



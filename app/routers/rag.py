"""RAG 制度问答 API。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.logger import logger
from app.services.rag.retriever import refresh_retriever
from app.services.rag.service import ask_rag
from app.services.rag.vector_store import index_all, get_document_stats, needs_reindex

router = APIRouter(prefix="/rag", tags=["RAG 制度问答"])


class AskRequest(BaseModel):
    question: str = Field(
        ..., min_length=1, max_length=500, description="中文自然语言问题"
    )


@router.post("/ask")
def rag_ask(body: AskRequest):
    """RAG 制度问答：根据企业制度文档回答用户问题。

    示例问题：
    - 加班超过多少小时算异常？
    - A级客户的合同金额标准是多少？
    - 双11期间的销售数据异常怎么判定？
    - 运营KPI的网站访问量目标值是多少？
    """
    result = ask_rag(body.question)
    return {"code": 200, "message": "ok", "data": result}


@router.post("/reindex")
def rag_reindex(force: bool = False):
    """重新索引制度文档（文档更新后调用）。

    force=False：per-document 增量，只重算变化的文档。
    force=True：全量重建（清空后重算所有文档）。
    """
    logger.info(f"收到重新索引请求 (force={force})")
    result = index_all(force=force)
    refresh_retriever()
    return {"code": 200, "message": "ok", "data": result}


@router.get("/documents")
def rag_documents():
    """获取已索引的文档清单和统计信息。"""
    stats = get_document_stats()
    needs_re = needs_reindex()
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "documents": stats,
            "total_documents": len(stats),
            "needs_reindex": needs_re,
        },
    }


@router.get("/health")
def rag_health():
    """RAG 服务健康检查。"""
    stats = get_document_stats()
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "status": "ready" if stats else "not_indexed",
            "indexed_documents": len(stats),
        },
    }

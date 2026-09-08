"""自然语言查询 API — LangChain Agent + RAG 双引擎驱动。

支持三种查询模式（自动判断）：
  - data_query：查结构化数据库（走 Agent）
  - doc_query：查非结构化制度文档（走 RAG）
  - hybrid：数据库 + 文档融合回答（LLM 合成统一答案）
"""

import asyncio
import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.logger import logger
from app.services.intent_router import classify_intent
from app.services.rag.service import ask_rag
from app.services.skills.hybrid import _fuse_hybrid_answer
from app.services.skills.registry import get_skill

router = APIRouter(prefix="/query", tags=["自然语言查询"])

# 意图置信度阈值：低于此值判定意图不可靠，强制走 hybrid 双引擎安全网（宁可多查不漏）
# 设为 0.6 而非 0.5：LLM 自评置信度普遍偏高约 0.1，阈值上移对冲，避免边界样本（真实 0.45 左右）被偏高推过线误走单引擎
INTENT_CONFIDENCE_THRESHOLD = 0.6


class QueryRequest(BaseModel):
    question: str = Field(
        ..., min_length=1, max_length=500, description="中文自然语言问题"
    )
    session_id: str = Field(
        default="",
        max_length=64,
        description="会话标识。同一会话传入相同 session_id 可保持多轮对话上下文。留空则每次独立查询。",
    )


def _save_trace(
    db: Session,
    session_id: str,
    question: str,
    answer: str,
    intent: str,
    mode: str,
    t0: float,
    iterations: int = 0,
    tools_used: list | None = None,
):
    """写入对话历史（chat_history）+ 查询 trace（query_trace）。

    trace 写入失败不影响主查询结果，所以整体包 try/except。
    """
    from app.models.chat_history import ChatHistory
    from app.models.query_trace import QueryTrace

    tools_used = tools_used or []
    latency_ms = round((time.time() - t0) * 1000, 1)
    try:
        db.add(ChatHistory(session_id=session_id, role="user", content=question))
        db.add(ChatHistory(session_id=session_id, role="assistant", content=answer))
        db.add(
            QueryTrace(
                session_id=session_id,
                question=question,
                answer=answer,
                intent=intent,
                mode=mode,
                iterations=iterations,
                tools_used=tools_used,
                latency_ms=latency_ms,
            )
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"trace 写入失败（不影响查询）: {e}")


@router.post("", response_model=dict)
async def natural_language_query(body: QueryRequest, db: Session = Depends(get_db)):
    """使用自然语言查询数据平台。

    背后是 LangChain ReAct Agent + 通义千问 + RAG 制度检索。
    多轮对话：传入 session_id，LangGraph Checkpointer 自动恢复上下文。

    示例问题：
    - 销售部11月异常率多少？          → Agent 查库
    - 加班超过多少小时算异常？        → RAG 查制度
    - 郑十为什么被标记为异常？        → hybrid LLM 融合
    """
    thread_id = body.session_id or "default"
    t0 = time.time()

    # 1. 意图分类
    intent_result = classify_intent(body.question)
    intent = intent_result["intent"]
    confidence = intent_result.get("confidence", 0.0)

    # 2. 低置信度兜底：信心不足时强制走 hybrid 双引擎，宁可多查不漏
    if confidence < INTENT_CONFIDENCE_THRESHOLD and intent != "hybrid":
        logger.info(
            f"意图 {intent} 置信度 {confidence:.2f} 低于阈值 "
            f"{INTENT_CONFIDENCE_THRESHOLD}，降级为 hybrid"
        )
        intent = "hybrid"

    # 3. 按意图路由：查 Skill 注册表，按需调度对应能力（新增能力只需注册，不改路由）
    skill = get_skill(intent)
    result = await skill.handler(body.question, thread_id)

    _save_trace(
        db, thread_id, body.question, result.answer, intent, result.mode, t0,
        result.iterations, result.tools_used,
    )

    # 按 mode 组装 API 响应（保持原有字段契约不变）
    if result.mode == "rag":
        data = {
            "answer": result.answer,
            "sources": result.extra.get("sources", []),
            "chunks_count": result.extra.get("chunks_count", 0),
            "mode": "rag",
            "intent": intent,
        }
    elif result.mode == "hybrid":
        data = {
            "question": body.question,
            "answer": result.answer,
            "mode": "hybrid",
            "intent": intent,
            "agent_data": {
                "iterations": result.iterations,
                "tools_used": result.tools_used,
            },
            "rag_data": result.extra.get("rag_data", {}),
        }
    else:  # agent
        data = {
            "question": body.question,
            "answer": result.answer,
            "mode": "agent",
            "intent": intent,
            "iterations": result.iterations,
            "tools_used": result.tools_used,
        }

    return {"code": 200, "message": "ok", "data": data}


# —— SSE 格式化 ——
def _sse(event: str, data: dict) -> str:
    """将事件格式化为 SSE (Server-Sent Events) 字符串。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _collect_agent_stream(question: str, thread_id: str = "default", intent: str = "data_query") -> tuple[list, str, int, list]:
    """收集 Agent 流式事件，返回 (中间事件列表, 最终答案, iterations, tools_used)。"""
    from app.services.query_agent import run_query_stream

    events = []
    final_answer = ""
    iterations = 0
    tools_used = []
    async for evt in run_query_stream(question, thread_id, intent=intent):
        if evt["event"] == "answer":
            final_answer = evt["data"].get("content", "")
            iterations = evt["data"].get("iterations", 0)
            tools_used = evt["data"].get("tools_used", [])
        elif evt["event"] == "error":
            final_answer = evt["data"].get("message", "查询服务暂时不可用")
            events.append(evt)
        elif evt["event"] not in ("done", "agent_start"):
            events.append(evt)
    return events, final_answer, iterations, tools_used


@router.post("/stream")
async def natural_language_query_stream(
    body: QueryRequest, db: Session = Depends(get_db)
):
    """流式自然语言查询 — SSE 实时推送 Agent 思考过程。

    事件类型:
      intent     — 意图分类结果
      agent_start — Agent 开始执行
      tool_call  — Agent 调用工具
      tool_result — 工具返回结果
      rag_ready  — RAG 检索完成
      answer     — 最终答案
      error      — 异常降级
      done       — 流结束

    前端示例:
      const es = new EventSource("/query/stream");
      es.addEventListener("tool_call", e => console.log(JSON.parse(e.data)));
    """
    from app.services.query_agent import run_query_stream

    thread_id = body.session_id or "default"

    async def event_generator():
        done_sent = False
        try:
            # 1. 意图分类
            intent_result = classify_intent(body.question)
            intent = intent_result["intent"]
            confidence = intent_result.get("confidence", 0.0)

            # 2. 低置信度兜底：信心不足时强制走 hybrid 双引擎
            if confidence < INTENT_CONFIDENCE_THRESHOLD and intent != "hybrid":
                logger.info(
                    f"意图 {intent} 置信度 {confidence:.2f} 低于阈值 "
                    f"{INTENT_CONFIDENCE_THRESHOLD}，降级为 hybrid"
                )
                intent = "hybrid"

            yield _sse("intent", {"intent": intent})

            if intent == "data_query":
                async for evt in run_query_stream(body.question, thread_id, intent=intent):
                    if evt["event"] == "done":
                        break
                    yield _sse(evt["event"], evt["data"])

            elif intent == "doc_query":
                result = await asyncio.to_thread(ask_rag, body.question)
                yield _sse(
                    "answer",
                    {
                        "content": result["answer"],
                        "sources": result.get("sources", []),
                        "chunks_count": result.get("chunks_count", 0),
                        "mode": "rag",
                    },
                )

            else:  # hybrid
                # 并行：Agent 流式 + RAG
                agent_task = asyncio.create_task(
                    _collect_agent_stream(body.question, thread_id, intent="hybrid")
                )
                rag_result = await asyncio.to_thread(ask_rag, body.question)

                agent_events, agent_answer, _, _ = await agent_task

                # 逐条推送 Agent 中间事件（tool_call / tool_result）
                for evt in agent_events:
                    yield _sse(evt["event"], evt["data"])

                # 推送 RAG 检索结果
                rag_sources = rag_result.get("sources", [])
                yield _sse(
                    "rag_ready",
                    {
                        "chunks_count": rag_result.get("chunks_count", 0),
                        "top_doc": rag_sources[0].get("doc_title", "")
                        if rag_sources
                        else "",
                    },
                )

                # 融合
                fused_answer = await asyncio.to_thread(
                    _fuse_hybrid_answer,
                    body.question,
                    agent_answer,
                    rag_result.get("answer", ""),
                    rag_sources,
                )
                yield _sse(
                    "answer",
                    {
                        "content": fused_answer,
                        "mode": "hybrid",
                        "intent": intent,
                    },
                )

            yield _sse("done", {})
            done_sent = True

        except Exception as e:
            logger.error(f"流式查询异常: {e}")
            yield _sse("error", {"message": "查询服务暂时不可用，请稍后重试。"})
        finally:
            if not done_sent:
                yield _sse("done", {})

    return StreamingResponse(event_generator(), media_type="text/event-stream")

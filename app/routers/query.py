"""自然语言查询 API — LangChain Agent + RAG 双引擎驱动。

支持三种查询模式（由意图路由自动判断）：
  - data_query：查结构化数据库（走 Agent）
  - doc_query：查非结构化制度文档（走 RAG）
  - hybrid：数据库 + 文档融合回答（LLM 合成统一答案）
"""

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.logger import logger
from app.services.intent_router import INTENT_CONFIDENCE_THRESHOLD, classify_intent
from app.services.rag.service import ask_rag
from app.services.skills.hybrid import _fuse_hybrid_answer
from app.services.skills.registry import get_skill

router = APIRouter(prefix="/query", tags=["自然语言查询"])


class QueryRequest(BaseModel):
    question: str = Field(
        ..., min_length=1, max_length=500, description="中文自然语言问题"
    )
    session_id: str = Field(
        default="",
        max_length=64,
        description="会话标识。同一会话传入相同 session_id 可保持多轮对话上下文。留空则每次独立查询。",
    )


async def _route_intent(question: str) -> tuple[str, dict, float]:
    """意图路由：读 logprobs 真实分布，低置信度时升级走安全网。

    低置信度不是降级，是路由——手里还有模型给出的真实分布，只是决定不信它，
    把它交给安全网。

    Returns:
        (最终 intent, 路由明细, 意图分段耗时 ms)
    """
    t = time.perf_counter()
    # 别阻塞事件循环：意图路由是一次同步 LLM 调用，在 async 上下文里直接调会卡住
    # 整个 loop。流式接口对阻塞的容忍度是零——一次阻塞卡的是所有并发连接，
    # 不是当前这一个。
    route = await asyncio.to_thread(classify_intent, question)
    intent = route["intent"]

    if route["confidence"] < INTENT_CONFIDENCE_THRESHOLD and intent != "hybrid":
        logger.info(
            f"意图 {intent} 置信度 {route['confidence']:.3f} 低于阈值 "
            f"{INTENT_CONFIDENCE_THRESHOLD}，升级走 hybrid 安全网"
        )
        intent = "hybrid"

    return intent, route, (time.perf_counter() - t) * 1000


def _save_trace(
    db: Session,
    trace_id: str,
    session_id: str,
    question: str,
    answer: str,
    intent: str,
    mode: str,
    latency_ms: float,
    stage_timings: dict,
    route: dict,
    iterations: int = 0,
    tools_used: list | None = None,
    tool_calls: list | None = None,
):
    """写入对话历史（chat_history）+ 查询 trace（query_trace）。

    trace 是旁路不是主路，整体包 try/except，写失败只告警，绝不影响查询结果。

    注意：latency_ms 是端到端墙钟，stage_timings 是分段诊断。hybrid 路径下
    Agent 与检索是并行的，两段耗时重叠，加总不等于总耗时——这也是为什么要分开存。
    """
    from app.models.chat_history import ChatHistory
    from app.models.query_trace import QueryTrace

    tools_used = tools_used or []
    tool_calls = tool_calls or []
    try:
        db.add(ChatHistory(session_id=session_id, role="user", content=question))
        db.add(ChatHistory(session_id=session_id, role="assistant", content=answer))
        db.add(
            QueryTrace(
                trace_id=trace_id,
                session_id=session_id,
                question=question,
                answer=answer,
                intent=intent,
                mode=mode,
                iterations=iterations,
                tools_used=tools_used,
                tool_calls=tool_calls,
                stage_timings={k: round(v, 1) for k, v in stage_timings.items()},
                route_path=route.get("path", ""),
                route_degrade=route.get("degrade", ""),
                p_top=round(route.get("p_top", 0.0), 6),
                label_mass=round(route.get("label_mass", 0.0), 6),
                route_margin=round(route.get("margin", 0.0), 6),
                top_logprobs=route.get("top5", {}),
                latency_ms=round(latency_ms, 1),
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
    trace_id = uuid.uuid4().hex
    t0 = time.time()
    stage_timings: dict[str, float] = {}

    # 1. 意图路由（logprobs 真实分布 + 低置信度升级安全网）
    intent, route, intent_ms = await _route_intent(body.question)
    stage_timings["intent"] = intent_ms

    # 2. 按意图路由：查 Skill 注册表，按需调度对应能力（新增能力只需注册，不改路由）
    skill = get_skill(intent)
    t_skill = time.perf_counter()
    result = await skill.handler(body.question, thread_id)
    stage_timings["agent" if result.mode == "agent" else "retrieval"] = (
        time.perf_counter() - t_skill
    ) * 1000

    _save_trace(
        db, trace_id, thread_id, body.question, result.answer, intent, result.mode,
        (time.time() - t0) * 1000, stage_timings, route,
        result.iterations, result.tools_used, result.tool_calls,
    )
    logger.info(f"查询完成 trace_id={trace_id} total={int(time.time() - t0)}s")

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


async def _collect_agent_stream(
    question: str, thread_id: str = "default", intent: str = "data_query"
) -> tuple[list, str, int, list, list]:
    """收集 Agent 流式事件，返回 (中间事件列表, 最终答案, iterations, tools_used, tool_calls)。"""
    from app.services.query_agent import run_query_stream

    events = []
    final_answer = ""
    iterations = 0
    tools_used = []
    tool_calls = []
    async for evt in run_query_stream(question, thread_id, intent=intent):
        if evt["event"] == "answer":
            final_answer = evt["data"].get("content", "")
            iterations = evt["data"].get("iterations", 0)
            tools_used = evt["data"].get("tools_used", [])
            tool_calls = evt["data"].get("tool_calls", [])
        elif evt["event"] == "error":
            final_answer = evt["data"].get("message", "查询服务暂时不可用")
            events.append(evt)
        elif evt["event"] not in ("done", "agent_start"):
            events.append(evt)
    return events, final_answer, iterations, tools_used, tool_calls


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
    trace_id = uuid.uuid4().hex

    async def event_generator():
        done_sent = False
        t_start = time.perf_counter()
        stage_timings: dict[str, float] = {}
        route: dict = {}
        intent = ""
        state = {
            "answer": "",
            "mode": "agent",
            "iterations": 0,
            "tools_used": [],
            "tool_calls": [],
        }
        try:
            # 1. 意图路由（logprobs 真实分布 + 低置信度升级安全网）
            intent, route, intent_ms = await _route_intent(body.question)
            stage_timings["intent"] = intent_ms

            # 第一帧就把意图推出去：首字延迟不等于总延迟，用户在第一帧就拿到
            # 「意图判成了 data_query」，这一帧就是意图路由的耗时。
            yield _sse("intent", {"intent": intent, "trace_id": trace_id})

            if intent == "data_query":
                async for evt in run_query_stream(body.question, thread_id, intent=intent):
                    if evt["event"] == "done":
                        break
                    if evt["event"] == "answer":
                        state["answer"] = evt["data"].get("content", "")
                        state["iterations"] = evt["data"].get("iterations", 0)
                        state["tools_used"] = evt["data"].get("tools_used", [])
                        state["tool_calls"] = evt["data"].get("tool_calls", [])
                    yield _sse(evt["event"], evt["data"])

            elif intent == "doc_query":
                state["mode"] = "rag"
                t = time.perf_counter()
                result = await asyncio.to_thread(ask_rag, body.question)
                stage_timings["retrieval"] = (time.perf_counter() - t) * 1000
                state["answer"] = result["answer"]
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
                state["mode"] = "hybrid"
                # 并行：Agent 流式 + RAG。两段耗时是同一段并行窗口的两条腿，会重叠。
                t_par = time.perf_counter()
                agent_task = asyncio.create_task(
                    _collect_agent_stream(body.question, thread_id, intent="hybrid")
                )
                rag_result = await asyncio.to_thread(ask_rag, body.question)
                stage_timings["retrieval"] = (time.perf_counter() - t_par) * 1000

                agent_events, agent_answer, iterations, tools_used, tool_calls = (
                    await agent_task
                )
                stage_timings["agent"] = (time.perf_counter() - t_par) * 1000
                state["answer"] = agent_answer
                state["iterations"] = iterations
                state["tools_used"] = tools_used
                state["tool_calls"] = tool_calls

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
                t_fuse = time.perf_counter()
                fused_answer = await asyncio.to_thread(
                    _fuse_hybrid_answer,
                    body.question,
                    agent_answer,
                    rag_sources,
                )
                stage_timings["fusion"] = (time.perf_counter() - t_fuse) * 1000
                state["answer"] = fused_answer
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
            logger.error(f"流式查询异常 trace_id={trace_id}: {e}")
            yield _sse("error", {"message": "查询服务暂时不可用，请稍后重试。"})
        finally:
            # 已推出去的 tool_result 撤不回来，所以错误处理不是「终止」而是「收尾」：
            # 告诉用户发生了什么，再明确结束。
            if not done_sent:
                yield _sse("done", {})
            # trace 是旁路，写失败只告警，绝不能影响已经推完的流
            try:
                _save_trace(
                    db, trace_id, thread_id, body.question, state["answer"], intent,
                    state["mode"], (time.perf_counter() - t_start) * 1000,
                    stage_timings, route, state["iterations"],
                    state["tools_used"], state["tool_calls"],
                )
            except Exception as e:
                logger.warning(f"流式 trace 写入失败（不影响查询）trace_id={trace_id}: {e}")

    return StreamingResponse(event_generator(), media_type="text/event-stream")

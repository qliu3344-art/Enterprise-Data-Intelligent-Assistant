"""自然语言查询 API — LangChain Agent + RAG 双引擎驱动。

支持三种查询模式（自动判断）：
  - data_query：查结构化数据库（走 Agent）
  - doc_query：查非结构化制度文档（走 RAG）
  - hybrid：数据库 + 文档融合回答（LLM 合成统一答案）
"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.logger import logger
from app.services.query_agent import run_query
from app.services.intent_router import classify_intent
from app.services.rag.service import ask_rag

router = APIRouter(prefix="/query", tags=["自然语言查询"])

# 混合模式并行调用线程池
_executor = ThreadPoolExecutor(max_workers=2)

# —— 融合 Prompt ——
FUSION_PROMPT = """你是一个专业的企业数据分析师。请综合以下两部分信息，回答用户问题。

## 数据库查询结果（结构化数据）
{agent_answer}

## 相关制度文档条款（公司政策和规定）
{rag_chunks}

## 回答要求
1. 先给出数据事实（具体数字），再结合制度条款解释原因
2. 用中文自然语言表达，清晰流畅，4-10 句话
3. 数据部分要精确引用数字，制度部分引用具体条款编号（如"第X条第X款"）
4. 如果数据结果与制度规定有关联（阈值/规则等），明确指出
5. 如果某部分信息缺失，诚实说明

## 用户问题
{question}

请回答："""


class QueryRequest(BaseModel):
    question: str = Field(
        ..., min_length=1, max_length=500, description="中文自然语言问题"
    )
    session_id: str = Field(
        default="",
        max_length=64,
        description="会话标识。同一会话传入相同 session_id 可保持多轮对话上下文。留空则每次独立查询。",
    )


def _fuse_hybrid_answer(
    question: str, agent_answer: str, rag_answer: str, rag_chunks: list[dict]
) -> str:
    """用 LLM 将 Agent 数据结果和 RAG 制度条款融合为统一自然语言回答。

    关键策略：不依赖 RAG 的 LLM 回答（可能会说"未找到关于这个人的信息"），
    而是直接用检索到的原始制度 chunks 作为融合上下文。
    """
    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_core.messages import HumanMessage

    from app.config import settings

    # 构建 chunks 上下文（sources 是扁平结构，含 text/doc_title/chapter）
    if rag_chunks:
        chunks_text_parts = []
        for i, chunk in enumerate(rag_chunks[:5], 1):
            header = f"[条款{i}] 《{chunk.get('doc_title', '')}》"
            if chunk.get("chapter"):
                header += f" — {chunk['chapter']}"
            chunks_text_parts.append(f"{header}\n{chunk.get('text', '')}")
        chunks_text = "\n\n".join(chunks_text_parts)
    else:
        chunks_text = "（未检索到相关制度条款）"

    # 如果没有检索到制度条款，直接返回 Agent 答案
    if not rag_chunks:
        return agent_answer

    prompt = FUSION_PROMPT.format(
        agent_answer=agent_answer[:2000],
        rag_chunks=chunks_text[:2500],
        question=question,
    )

    try:
        llm = ChatTongyi(
            model=settings.LLM_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.1,
            max_tokens=800,
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content
    except Exception as e:
        logger.error(f"融合回答生成失败: {e}")
        # 降级：数据结果 + RAG 回答拼合
        if rag_answer and "未找到" not in rag_answer:
            return f"{agent_answer}\n\n📋 制度依据：\n{rag_answer}"
        return agent_answer


@router.post("", response_model=dict)
def natural_language_query(body: QueryRequest, db: Session = Depends(get_db)):
    """使用自然语言查询数据平台。

    背后是 LangChain ReAct Agent + 通义千问 + RAG 制度检索。
    多轮对话：传入 session_id，LangGraph Checkpointer 自动恢复上下文。

    示例问题：
    - 销售部11月异常率多少？          → Agent 查库
    - 加班超过多少小时算异常？        → RAG 查制度
    - 郑十为什么被标记为异常？        → hybrid LLM 融合
    """
    thread_id = body.session_id or "default"

    # 1. 意图分类
    intent_result = classify_intent(body.question)
    intent = intent_result["intent"]

    # 2. 按意图路由
    if intent == "doc_query":
        result = ask_rag(body.question)
        result["mode"] = "rag"
        result["intent"] = intent
        return {"code": 200, "message": "ok", "data": result}
    elif intent == "hybrid":
        # 并行调用 Agent + RAG（节省 ~50% 延迟）
        agent_future = _executor.submit(run_query, body.question, db, thread_id)
        rag_result = ask_rag(body.question)
        agent_result = agent_future.result()
        rag_answer = rag_result.get("answer", "")
        rag_sources = rag_result.get("sources", [])
        final_answer = _fuse_hybrid_answer(
            question=body.question,
            agent_answer=agent_result["answer"],
            rag_answer=rag_answer,
            rag_chunks=rag_sources,
        )
        result = {
            "code": 200,
            "message": "ok",
            "data": {
                "question": body.question,
                "answer": final_answer,
                "mode": "hybrid",
                "intent": intent,
                "agent_data": {
                    "iterations": agent_result.get("iterations", 0),
                    "tools_used": agent_result.get("tools_used", []),
                },
                "rag_data": {
                    "chunks_count": rag_result.get("chunks_count", 0),
                    "sources": rag_sources,
                },
            },
        }
    else:
        agent_result = run_query(body.question, db, thread_id)
        agent_result["mode"] = "agent"
        agent_result["intent"] = intent
        result = {"code": 200, "message": "ok", "data": agent_result}

    return result


# —— SSE 格式化 ——
def _sse(event: str, data: dict) -> str:
    """将事件格式化为 SSE (Server-Sent Events) 字符串。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _collect_agent_stream(question: str, thread_id: str = "default") -> tuple[list, str]:
    """收集 Agent 流式事件，返回 (中间事件列表, 最终答案)。"""
    from app.services.query_agent import run_query_stream

    events = []
    final_answer = ""
    async for evt in run_query_stream(question, thread_id):
        if evt["event"] == "answer":
            final_answer = evt["data"].get("content", "")
        elif evt["event"] == "error":
            final_answer = evt["data"].get("message", "查询服务暂时不可用")
            events.append(evt)
        elif evt["event"] not in ("done", "agent_start"):
            events.append(evt)
    return events, final_answer


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
        try:
            # 1. 意图分类
            intent_result = classify_intent(body.question)
            intent = intent_result["intent"]
            yield _sse("intent", {"intent": intent})

            if intent == "data_query":
                final_answer = ""
                async for evt in run_query_stream(body.question, thread_id):
                    if evt["event"] == "answer":
                        final_answer = evt["data"].get("content", "")
                        yield _sse(evt["event"], evt["data"])
                    elif evt["event"] == "done":
                        break
                    else:
                        yield _sse(evt["event"], evt["data"])
                yield _sse("done", {})

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
                yield _sse("done", {})

            else:  # hybrid
                # 并行：Agent 流式 + RAG
                agent_task = asyncio.create_task(
                    _collect_agent_stream(body.question, thread_id)
                )
                rag_result = await asyncio.to_thread(ask_rag, body.question)

                agent_events, agent_answer = await agent_task

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

        except Exception as e:
            logger.error(f"流式查询异常: {e}")
            yield _sse("error", {"message": "查询服务暂时不可用，请稍后重试。"})
            yield _sse("done", {})

    return StreamingResponse(event_generator(), media_type="text/event-stream")

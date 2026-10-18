"""LangChain 数据分析 Agent — 自然语言查询。

用户用中文提问（"销售部11月异常率多少？"），Agent 自动：
  1. 理解意图 → 2. 选择工具 → 3. 执行查询 → 4. 解释结果

涉及的 LangChain / LangGraph 核心概念：
  - ChatTongyi：通义千问 LLM 的 LangChain 封装
  - @tool：使用 @tool 装饰器定义自定义工具
  - create_agent：LangChain 1.x 的 Agent 工厂（基于 LangGraph）
  - AsyncPostgresSaver Checkpointer：会话状态落 Postgres，通过 thread_id 恢复上下文
  - Agent 自动完成 Thought → Action → Observation 循环
"""

import asyncio

import pandas as pd
from sqlalchemy.orm import Session

# —— LangChain 1.x imports ——
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage

from app.config import settings
from app.logger import logger
from app.services.context_manager import compress_history
from app.services.memory_store import (
    build_convention_message,
    extract_conventions,
    save_convention,
)

# —— 全局 Checkpointer 单例 ——
# 所有 Agent 实例共享同一个 checkpointer，通过 thread_id 隔离会话。
#
# 落 Postgres 而不是 SQLite：会话状态是权威数据，要跨实例共享、要扛住「每轮都写」
# 的高频小事务。SQLite 是库级写锁，写入只能排队，攒到几百人并发就抛 database is
# locked——而且是间歇性的、平时不复现。
#
# 用 AsyncPostgresSaver 而不是 PostgresSaver：同一个 graph 上既有同步调用也有异步
# 调用（astream 走 SSE 出口），而 saver 是一类只管一种模式——同步版配 astream 会抛
# "does not support async methods"，异步版配 invoke 会抛镜像的那句。所以要么全同步、
# 要么全异步，这里统一走异步。

_init_lock = asyncio.Lock()


async def _create_checkpointer():
    """创建会话检查点。**失败即抛，没有降级。**

    Postgres 在这个项目里是硬依赖——业务数据、向量、会话检查点三样都在里面，
    所以「PG 挂了但应用还能用」这个状态根本不存在。既然不存在，回退
    MemorySaver 换来的就不是「降级可用」，而是「看起来健康、实际静默丢记忆」：
    接口照常 200、日志干净、监控不响，只有对话历史悄悄不落盘。
    拿不到权威状态就不该假装健康，所以宁可起不来。

    惰性创建而不是模块级立即执行：eval.py 直接 import run_query 跑评测，不经过
    FastAPI 的 startup 钩子，那边没有现成的事件循环可用。

    调用方须持有 _init_lock。
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    # 先直接判循环类型，而不是等 psycopg 报错再去认那句错误字符串：
    # pool.open() 默认不等连接（wait=False，立刻返回），真正的连接失败是后面的
    # setup() 撞上的，那时异常已经是连接池包装过的 PoolTimeout —— 原始的
    # 「Psycopg cannot use the 'ProactorEventLoop'」只留在池子自己的日志里，
    # 从异常对象上拿不到。所以这里主动查，而不是被动认。
    # 现在这条错误会让服务起不来，报准了能省一次排查。
    if isinstance(asyncio.get_running_loop(), getattr(asyncio, "ProactorEventLoop", ())):
        raise RuntimeError(
            "psycopg 的异步模式不能跑在 Windows 默认的 ProactorEventLoop 上。"
            "启动 uvicorn 时必须带 --loop app.main:selector_loop_factory，"
            "详见 app/main.py::selector_loop_factory。"
        )

    # autocommit：checkpointer 自己管事务边界
    # prepare_threshold=0：连接池里禁用预备语句缓存
    pool = AsyncConnectionPool(
        conninfo=settings.CHECKPOINT_DB_URL,
        min_size=1,
        max_size=10,
        open=False,
        timeout=5,  # 只影响建立连接，不拖慢查询
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
        },
    )
    try:
        await pool.open()
        saver = AsyncPostgresSaver(pool)
        await saver.setup()  # 幂等：CREATE TABLE IF NOT EXISTS
    except Exception as e:
        # 建到一半失败就把池子关掉，别留一堆后台重连任务在打日志。
        # 库没建 / 口令不对 / PG 没起，都从这里出来。
        await pool.close()
        raise RuntimeError(
            f"会话检查点初始化失败（库 {settings.CHECKPOINT_DB}）：{e}"
        ) from e
    logger.info(f"会话检查点：AsyncPostgresSaver @ {settings.CHECKPOINT_DB}")
    return saver

# —— System Prompt：指导 LLM 使用工具并输出结构化答案 ——
SYSTEM_PROMPT = """你是一个专业的数据分析助手，可以访问企业数据智能助手的数据库。

你会收到对应的「操作手册」（skill 指令），请严格按照手册中的工具使用说明和决策树执行。

## 回答要求
- 工具返回空结果时诚实告知，不编造数据
- 用中文自然语言回答，3-8 句话，包含具体数字
- 问"哪个最好/最差"时要主动比较后给出明确判断

## 数据类型参考
- attendance: 考勤（出勤天数、请假天数、加班时长、迟到次数）
- sales: 销售（订单金额、订单数量、产品名称、客户名称、支付方式）
- customer: 客户（客户名称、等级、合同金额、联系电话、跟进日期）
- operation: 运营（指标名称、数值、单位、目标值、完成率）
"""


def _persist_conventions(question: str, answer: str, session_id: str) -> None:
    """提取并沉淀这一轮交代的业务口径。

    阻塞实现（里面有一次 embedding 和可能的一次 LLM 冲突判定），调用方负责
    丢线程池 —— 直接在事件循环上跑会把同进程的 SSE 长连接一起卡住。

    作用域由提取器一并判出：会话级临时限定在这里被 save_convention 丢掉，
    它本来就该留在对话上下文里，不该进长期记忆。
    """
    for c in extract_conventions(question, answer):
        save_convention(
            c["content"],
            scope=c["scope"],
            department=c["department"],
            session_id=session_id,
        )


def _load_skill_instructions(intent: str) -> str:
    """按 intent 加载对应 skill 的操作手册（惰性 import，避免循环依赖）。

    data_query / hybrid 都需要数据查询的操作手册；doc_query 走 RAG 不经 Agent，
    返回空串（不注入）。
    """
    if intent not in ("data_query", "hybrid"):
        return ""
    from app.services.skills.instructions import DATA_QUERY_INSTRUCTIONS

    return DATA_QUERY_INSTRUCTIONS


# —— 缓存 Agent 实例 ——
# 避免每次查询都重新创建 Agent（涉及 LangGraph 状态图编译）
_agent_instance = None


async def get_agent():
    """获取或创建缓存的 Agent 实例（懒加载；并发的首次调用只建一次）。"""
    global _agent_instance
    if _agent_instance is not None:
        return _agent_instance
    async with _init_lock:
        if _agent_instance is None:
            _agent_instance = _build_agent(await _create_checkpointer())
    return _agent_instance


def _build_agent(checkpointer):
    """构建 Agent（内部函数，仅首次调用时执行）。"""

    # —— 工具定义 ——

    @tool
    def query_cleaned_records(
        data_type: str = "",
        department: str = "",
        date_start: str = "",
        date_end: str = "",
        limit: int = 20,
    ) -> str:
        """查询清洗后的标准化数据记录。可筛选数据类型、部门、日期范围。"""
        from app.services.data_query_ops import query_cleaned_records as _op
        return _op(data_type, department, date_start, date_end, limit)

    @tool
    def get_summary_stats(data_type: str = "") -> str:
        """获取汇总统计数据。返回各类型的记录数、异常率、平均质量分。"""
        from app.services.data_query_ops import get_summary_stats as _op
        return _op(data_type)

    @tool
    def get_anomaly_details(
        data_type: str = "",
        department: str = "",
        employee_name: str = "",
        limit: int = 10,
    ) -> str:
        """查询异常记录的详细信息，包括异常原因和业务数据。

        可按数据类型、部门、员工姓名筛选。查具体人时用 employee_name 参数。
        """
        from app.services.data_query_ops import get_anomaly_details as _op
        return _op(data_type, department, employee_name, limit)

    # —— 组装 Agent ——
    tools = [query_cleaned_records, get_summary_stats, get_anomaly_details]

    llm = ChatTongyi(
        model=settings.LLM_MODEL,
        dashscope_api_key=settings.DASHSCOPE_API_KEY,
        temperature=0.1,
        max_tokens=1000,
        # 不传的话 SDK 用 300 秒默认值，网络抖一下这个请求就挂满 5 分钟
        model_kwargs={"request_timeout": settings.LLM_REQUEST_TIMEOUT_SECONDS},
    )

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )

    return agent


def _json_to_text(raw: str) -> str:
    """将 Agent 返回的原始 JSON 强制转换为可读的中文自然语言。"""
    import json as _json
    import re as _re

    # 先清理 markdown 代码块
    text = _re.sub(r"```json|```", "", raw).strip()

    try:
        data = _json.loads(text)
    except (_json.JSONDecodeError, ValueError):
        # 不是纯 JSON，可能混合了文字和 JSON，尽力提取
        # 找到第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = _json.loads(text[start:end + 1])
            except (_json.JSONDecodeError, ValueError):
                return text  # 放弃，返回原文
        else:
            return text

    if not isinstance(data, dict):
        return str(data)

    parts = []

    # 处理异常列表格式
    if "anomalies" in data and isinstance(data["anomalies"], list):
        items = data["anomalies"]
        total = data.get("total_anomalies", len(items))
        if total == 0:
            return "查询结果：没有找到异常记录，数据质量良好。"
        parts.append(f"共找到 {total} 条异常记录，以下是关键信息：\n")
        for i, a in enumerate(items[:8], 1):
            name = a.get("employee_name", "未知")
            dept = a.get("department", "未知")
            dtype = a.get("data_type", "未知")
            reason = a.get("reason", "")
            biz = a.get("business_data", {})
            # 从 business_data 中提取更准确的信息
            if isinstance(biz, dict):
                actual_name = biz.get("姓名") or name
                actual_dept = biz.get("所属部门") or dept
                hours = biz.get("加班时长(小时)", "")
                days = biz.get("出勤天数", "")
                parts.append(f"{i}. {actual_name}（{actual_dept}）")
                if hours:
                    parts.append(f"   加班 {hours} 小时，出勤 {days} 天")
            else:
                parts.append(f"{i}. {name}（{dept}）")
            if reason and len(reason) > 10:
                # 截取前 80 字
                short = reason[:80].strip()
                if len(reason) > 80:
                    short += "..."
                parts.append(f"   原因：{short}")
            parts.append("")
        return "\n".join(parts)

    # 处理汇总统计格式
    if "总记录数" in data or "total_records" in str(data):
        total = data.get("总记录数", data.get("total_records", "?"))
        anomalies = data.get("异常记录数", data.get("anomaly_count", "?"))
        rate = data.get("异常率(%)", data.get("anomaly_rate", "?"))
        score = data.get("平均质量分", data.get("avg_quality_score", "?"))
        parts.append(f"平台共有 {total} 条清洗后数据，异常 {anomalies} 条（异常率 {rate}%），平均质量分 {score}。")

        by_type = data.get("按类型分组", data.get("by_type", {}))
        if by_type:
            parts.append("各类型分布：")
            for dt, info in by_type.items():
                if isinstance(info, dict):
                    cnt = info.get("记录数", info.get("record_count", "?"))
                    ar = info.get("异常率(%)", info.get("anomaly_rate", "?"))
                    parts.append(f"  {dt}：{cnt} 条，异常率 {ar}%")
        return "\n".join(parts)

    # 兜底：纯文本化 JSON
    return _json.dumps(data, ensure_ascii=False, indent=2)


async def _apply_context_compression(agent, config: dict, question: str):
    """三级上下文管理：长期记忆召回 + 滑动窗口 + 摘要压缩。

    读历史 → 召回长期记忆 → 压缩历史（窗口+摘要）→ 覆盖写回 checkpointer，
    防止多轮对话历史无限膨胀、稀释注意力。

    Returns:
        长期记忆 SystemMessage，无相关记忆时返回 None。
    """
    # 1. 读历史
    try:
        state = await agent.aget_state(config)
    except Exception as e:
        logger.warning(f"读取 checkpointer 历史失败: {e}")
        state = None
    history = list(state.values.get("messages", [])) if state and state.values else []

    # 2、3 两步是同步实现（embedding 与 LLM 调用都会阻塞），丢进线程池——
    # 否则会把 SSE 那条长连接所在的事件循环一起卡住，一次几秒。
    # 2. 长期记忆召回。带上 thread_id：候选口径只对它诞生的那个会话可见，
    #    拿到的是「本会话立即生效、全局生效要审核」里的前一半。
    memory_msg = await asyncio.to_thread(
        build_convention_message, question, config.get("configurable", {}).get("thread_id")
    )

    # 3. 滑动窗口 + 摘要压缩
    compressed = await asyncio.to_thread(compress_history, history)

    # 4. 覆盖写回 checkpointer（先删旧、再写压缩后，避免历史膨胀）
    if history:
        delete_msgs = [RemoveMessage(id=m.id) for m in history if getattr(m, "id", None)]
        try:
            await agent.aupdate_state(config, {"messages": delete_msgs})
        except Exception as e:
            logger.warning(f"清空 checkpointer 历史失败: {e}")
    if compressed:
        try:
            await agent.aupdate_state(config, {"messages": compressed})
        except Exception as e:
            logger.warning(f"写回压缩历史失败: {e}")

    return memory_msg


async def run_query(question: str, db: Session = None, thread_id: str = "default", intent: str = "data_query") -> dict:
    """执行一次自然语言数据查询。

    Args:
        question: 用户当前问题
        db: 数据库会话（保留兼容）
        thread_id: LangGraph 会话标识。同一 thread_id 自动恢复对话上下文。
        intent: 意图（data_query/doc_query/hybrid），用于按需加载对应 skill 操作手册
    """
    import re

    logger.info(f"LangChain Agent 查询: {question[:100]} (thread={thread_id[:12]})")

    config = {"configurable": {"thread_id": thread_id}}
    try:
        agent = await get_agent()

        # —— 三级上下文管理：长期记忆召回 + 滑动窗口 + 摘要压缩 ——
        memory_msg = await _apply_context_compression(agent, config, question)

        input_messages = []
        if memory_msg is not None:
            input_messages.append(memory_msg)
        # 按意图注入 skill 操作手册（data_query/hybrid）
        instructions = _load_skill_instructions(intent)
        if instructions:
            input_messages.append(SystemMessage(content=instructions))
        input_messages.append(HumanMessage(content=question))

        result = await agent.ainvoke({"messages": input_messages}, config=config)
    except Exception as e:
        logger.error(f"Agent 执行失败: {e}")
        return {
            "question": question,
            "answer": "查询服务暂时不可用，请稍后重试。",
            "iterations": 0,
            "tools_used": [],
            "tool_calls": [],
        }

    messages = result.get("messages", [])
    answer = ""
    tools_used = []
    tool_calls = []

    # ⚠️ 这两个分支必须是 if / elif，不能拆成两个并列的 if。
    # AIMessage 永远带 tool_calls 属性（值是空列表也算「有」），而 System / Human /
    # Tool 消息都没有。所以写成 `not hasattr(msg, "tool_calls")` 等于「跳过所有 AI 消息」：
    # answer 会停在最后一条 Tool 或 Human 消息上——调了工具就返回原始 dump、
    # 没调工具就直接把用户的问题当答案返回（现象是「回声」）。
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tools_used.append(tc.get("name", "unknown"))
                tool_calls.append(
                    {"name": tc.get("name", "unknown"), "args": tc.get("args", {})}
                )
        elif hasattr(msg, "content") and msg.content:
            answer = msg.content

    if not answer and messages:
        last = messages[-1]
        answer = last.content if hasattr(last, "content") else str(last)

    # 强制转换：检测到 JSON 代码块或纯 JSON 时，自动转为自然语言
    answer_stripped = answer.strip()
    if answer_stripped.startswith("{") or answer_stripped.startswith("```"):
        logger.warning("Agent 返回了原始 JSON，强制转换为自然语言...")
        answer = _json_to_text(answer_stripped)

    iterations = len(tools_used)
    logger.info(f"Agent 完成: iterations={iterations}, answer_len={len(answer)}")

    # 沉淀业务口径（用户交代过的规则/定义，供后续跨会话遵循）
    await asyncio.to_thread(_persist_conventions, question, answer, thread_id)

    return {
        "question": question,
        "answer": answer,
        "iterations": iterations,
        "tools_used": tools_used,
        # 工具名 + 实际参数，供评测核对参数是否填对（tools_used 保持原样不破坏调用方）
        "tool_calls": tool_calls,
    }


async def run_query_stream(question: str, thread_id: str = "default", intent: str = "data_query"):
    """流式执行 Agent 查询，yield SSE 事件字典。

    Args:
        question: 用户当前问题
        thread_id: LangGraph 会话标识。同一 thread_id 自动恢复对话上下文。
        intent: 意图（data_query/doc_query/hybrid），用于按需加载对应 skill 操作手册
    """
    logger.info(f"Agent 流式查询: {question[:100]} (thread={thread_id[:12]})")

    config = {"configurable": {"thread_id": thread_id}}

    try:
        agent = await get_agent()

        # —— 三级上下文管理：长期记忆召回 + 滑动窗口 + 摘要压缩 ——
        memory_msg = await _apply_context_compression(agent, config, question)

        yield {"event": "agent_start", "data": {"question": question}}

        final_answer = ""
        tools_used = []
        tool_calls = []

        input_messages = []
        if memory_msg is not None:
            input_messages.append(memory_msg)
        # 按意图注入 skill 操作手册（data_query/hybrid）
        instructions = _load_skill_instructions(intent)
        if instructions:
            input_messages.append(SystemMessage(content=instructions))
        input_messages.append(HumanMessage(content=question))

        async for chunk in agent.astream(
            {"messages": input_messages},
            config=config,
            stream_mode="updates",
        ):
            for node_name, node_output in chunk.items():
                # 节点名是 LangChain 1.x create_agent 定的：model / tools。
                # 写成 "agent" 匹配不上——不报错，只是 tool_call 事件永不触发、
                # final_answer 永远是空串，SSE 推出去一个空答案。
                if node_name == "model":
                    msgs = node_output.get("messages", [])
                    for msg in msgs:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tool_name = tc.get("name", "unknown")
                                tool_args = tc.get("args", {})
                                tools_used.append(tool_name)
                                # 与 SSE 推出去的是同一个对象——一次采集，多个出口
                                tool_calls.append({"name": tool_name, "args": tool_args})
                                yield {
                                    "event": "tool_call",
                                    "data": {
                                        "tool": tool_name,
                                        "args": tool_args,
                                    },
                                }
                        elif hasattr(msg, "content") and msg.content:
                            final_answer = msg.content

                elif node_name == "tools":
                    msgs = node_output.get("messages", [])
                    for msg in msgs:
                        content = msg.content if hasattr(msg, "content") else str(msg)
                        # 截断过长内容，避免 SSE 帧过大
                        if len(content) > 800:
                            content = content[:800] + "..."
                        yield {
                            "event": "tool_result",
                            "data": {"content": content},
                        }

        # 后处理：JSON → 自然语言转换
        if final_answer:
            answer_stripped = final_answer.strip()
            if answer_stripped.startswith("{") or answer_stripped.startswith("```"):
                logger.warning("Agent 流式返回了原始 JSON，强制转换为自然语言...")
                final_answer = _json_to_text(answer_stripped)

        yield {
            "event": "answer",
            "data": {
                "content": final_answer,
                "iterations": len(tools_used),
                "tools_used": tools_used,
                "tool_calls": tool_calls,
            },
        }

        # 沉淀业务口径。放在 answer 之后：答案已经在用户屏幕上了，这一步
        # （可能有一次 LLM 调用）最多只让 done 晚一点，不挡答案。
        #
        # 旧实现的流式路径上完全没有这一步，而前端走的就是这条路 ——
        # 口径记忆实际上只在不走 SSE 的 /query 接口上生效。
        await asyncio.to_thread(_persist_conventions, question, final_answer, thread_id)

    except Exception as e:
        logger.error(f"Agent 流式执行失败: {e}")
        yield {
            "event": "error",
            "data": {"message": "查询服务暂时不可用，请稍后重试。"},
        }

    yield {"event": "done", "data": {}}

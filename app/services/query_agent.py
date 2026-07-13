"""LangChain 数据分析 Agent — 自然语言查询。

用户用中文提问（"销售部11月异常率多少？"），Agent 自动：
  1. 理解意图 → 2. 选择工具 → 3. 执行查询 → 4. 解释结果

涉及的 LangChain / LangGraph 核心概念：
  - ChatTongyi：通义千问 LLM 的 LangChain 封装
  - @tool：使用 @tool 装饰器定义自定义工具
  - create_agent：LangChain 1.x 的 Agent 工厂（基于 LangGraph）
  - MemorySaver Checkpointer：LangGraph 原生状态持久化，通过 thread_id 恢复对话上下文
  - Agent 自动完成 Thought → Action → Observation 循环
"""

import pandas as pd
from sqlalchemy.orm import Session

# —— LangChain 1.x imports ——
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from app.config import settings
from app.logger import logger

# —— 全局 Checkpointer 单例 ——
# 所有 Agent 实例共享同一个 checkpointer，通过 thread_id 隔离会话
_checkpointer = MemorySaver()

# —— System Prompt：指导 LLM 使用工具并输出结构化答案 ——
SYSTEM_PROMPT = """你是一个专业的数据分析助手，可以访问自动化客户数据处理平台的数据库。

## 可用工具（按场景选择，不要无脑先用 get_summary_stats）

### query_cleaned_records — 查明细记录
查具体人、具体条件的原始数据行。适用场景：
  - 问某个人/某部门的业务数据（"张三的销售业绩"、"技术部的考勤记录"）
  - 按条件筛选记录（"合同金额>10万的客户"、"跟进日期超过30天的"）
  - 需要看逐条明细而非汇总数字时
参数：data_type（考勤/销售/客户/运营）、department、date_start、date_end、limit

### get_summary_stats — 查整体统计
查汇总指标、排名、分布。适用场景：
  - 问整体数据概况（"异常率多少"、"数据质量怎么样"）
  - 问排名/对比（"各部门异常率排名"、"哪个部门最好/最差"）
  - 问汇总数字而非具体人明细时
参数：data_type（可选，不传则查全部）

### get_anomaly_details — 查异常原因
查被标记为异常的记录及其原因。适用场景：
  - 问"为什么异常"、"有哪些异常记录"、"异常原因是什么"
  - 问某部门/某人的异常情况时
参数：data_type、department、employee_name、limit

## 工具选择决策树
1. 问题涉及"具体人 + 业务数据"（如张三的业绩、李四的考勤）→ 用 query_cleaned_records
2. 问题涉及"整体统计/排名/对比/比率"（如异常率、排名、哪个最好）→ 用 get_summary_stats
3. 问题涉及"异常原因/异常标记" → 用 get_anomaly_details
4. 不确定时，先想清楚用户要的是"一条条的明细"还是"汇总后的数字"

## 参数提示
- 问具体人时传 employee_name（如"郑十"、"张三"）
- 问具体部门时传 department（如"技术部"、"销售部"）

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


def _get_db():
    """获取线程安全的数据库会话（用于 Agent 工具内部）。"""
    from app.database import SessionLocal
    return SessionLocal()


def create_analysis_agent():
    """创建一个数据分析 Agent，基于 LangChain 1.x create_agent。

    每个工具函数内部独立获取 scoped_session，保证在 LangGraph
    多线程并发调用工具时，各线程拥有独立的 DB 连接，不会相互干扰。
    """

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
        from app.models.cleaned_record import CleanedRecord

        db = _get_db()
        try:
            query = db.query(CleanedRecord)
            if data_type:
                query = query.filter(CleanedRecord.data_type == data_type)
            if department:
                query = query.filter(
                    (CleanedRecord.department == department) |
                    (CleanedRecord.business_data['所属部门'].as_string() == department)
                )
            if date_start:
                query = query.filter(CleanedRecord.record_date >= date_start)
            if date_end:
                query = query.filter(CleanedRecord.record_date <= date_end)

            total = query.count()
            records = query.limit(limit).all()

            if not records:
                return "查询结果：没有匹配的数据记录。"

            lines = [f"查询结果：共 {total} 条匹配记录，以下展示前 {min(total, limit)} 条："]
            for i, r in enumerate(records, 1):
                name = r.employee_name or "未知"
                dept = r.department or "未知"
                dtype = r.data_type or "未知"
                date = str(r.record_date) if r.record_date else "未知"
                flag = "⚠异常" if r.is_anomaly else "正常"
                reason = f"，原因：{r.anomaly_reason}" if r.is_anomaly and r.anomaly_reason else ""
                biz = ""
                if r.business_data:
                    biz_items = [f"{k}={v}" for k, v in list(r.business_data.items())[:5]]
                    biz = f"，业务数据：{'，'.join(biz_items)}"
                lines.append(f"  {i}. [{dtype}] {name}（{dept}）{date} {flag}{reason}{biz}")
            return "\n".join(lines)
        finally:
            db.close()

    @tool
    def get_summary_stats(data_type: str = "") -> str:
        """获取汇总统计数据。返回各类型的记录数、异常率、平均质量分。"""
        from app.models.cleaned_record import CleanedRecord

        db = _get_db()
        try:
            query = db.query(CleanedRecord)
            if data_type:
                query = query.filter(CleanedRecord.data_type == data_type)

            records = query.all()
            if not records:
                return "暂无数据可统计。请先采集并清洗数据。"

            total = len(records)
            anomaly_count = sum(1 for r in records if r.is_anomaly)
            anomaly_rate = round(anomaly_count / total * 100, 2)
            avg_quality = round(sum(r.quality_score or 1.0 for r in records) / total, 4)

            type_groups: dict = {}
            for r in records:
                dt = r.data_type or "unknown"
                if dt not in type_groups:
                    type_groups[dt] = {"total": 0, "anomalies": 0, "score_sum": 0.0}
                type_groups[dt]["total"] += 1
                if r.is_anomaly:
                    type_groups[dt]["anomalies"] += 1
                type_groups[dt]["score_sum"] += r.quality_score or 1.0

            dept_groups: dict = {}
            for r in records:
                dept = r.department or "未知部门"
                if dept not in dept_groups:
                    dept_groups[dept] = {"total": 0, "anomalies": 0}
                dept_groups[dept]["total"] += 1
                if r.is_anomaly:
                    dept_groups[dept]["anomalies"] += 1

            TYPE_NAMES = {"attendance": "考勤", "sales": "销售", "customer": "客户", "operation": "运营"}

            lines = [
                "数据统计报告：",
                f"  总记录数：{total} 条",
                f"  异常记录数：{anomaly_count} 条",
                f"  整体异常率：{anomaly_rate}%",
                f"  平均质量分：{avg_quality}",
                "",
                "按数据类型分布：",
            ]
            for dt in ["attendance", "sales", "customer", "operation"]:
                if dt in type_groups:
                    g = type_groups[dt]
                    ar = round(g["anomalies"] / g["total"] * 100, 2)
                    ascore = round(g["score_sum"] / g["total"], 4)
                    lines.append(f"  {TYPE_NAMES.get(dt, dt)}：{g['total']} 条，异常 {g['anomalies']} 条（{ar}%），质量分 {ascore}")

            lines.append("")
            lines.append("按部门分布：")
            for dept, g in sorted(dept_groups.items()):
                ar = round(g["anomalies"] / g["total"] * 100, 2)
                lines.append(f"  {dept}：{g['total']} 条，异常率 {ar}%")

            return "\n".join(lines)
        finally:
            db.close()

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
        from app.models.cleaned_record import CleanedRecord


        db = _get_db()
        try:
            query = db.query(CleanedRecord).filter(CleanedRecord.is_anomaly == True)
            if data_type:
                query = query.filter(CleanedRecord.data_type == data_type)
            if department:
                query = query.filter(
                    (CleanedRecord.department == department) |
                    (CleanedRecord.business_data['所属部门'].as_string() == department)
                )
            if employee_name:
                # 同时搜索 employee_name 列和 business_data JSON 中的姓名
                query = query.filter(
                    (CleanedRecord.employee_name == employee_name) |
                    (CleanedRecord.business_data['姓名'].as_string() == employee_name)
                )

            total = query.count()
            records = query.limit(limit).all()

            if not records:
                return "异常记录查询结果：没有找到匹配的异常记录，数据质量良好。"

            TYPE_NAMES = {"attendance": "考勤", "sales": "销售", "customer": "客户", "operation": "运营"}

            lines = [f"异常记录报告：共 {total} 条异常，以下展示前 {min(total, limit)} 条："]
            for i, r in enumerate(records, 1):
                name = r.employee_name or "未知"
                dept = r.department or "未知"
                dt = TYPE_NAMES.get(r.data_type, r.data_type or "未知")
                date = str(r.record_date) if r.record_date else "未知"
                reason = r.anomaly_reason or "LLM 判定为异常"
                if len(reason) > 120:
                    reason = reason[:120] + "..."

                lines.append(f"  {i}. [{dt}] {name}（{dept}）{date}")
                lines.append(f"     异常原因：{reason}")

                if r.business_data:
                    biz_items = []
                    for k, v in list(r.business_data.items())[:5]:
                        biz_items.append(f"{k}={v}")
                    lines.append(f"     业务数据：{'，'.join(biz_items)}")

            return "\n".join(lines)
        finally:
            db.close()

    # —— 组装 Agent ——
    tools = [query_cleaned_records, get_summary_stats, get_anomaly_details]

    llm = ChatTongyi(
        model=settings.LLM_MODEL,
        dashscope_api_key=settings.DASHSCOPE_API_KEY,
        temperature=0.1,
        max_tokens=1000,
    )

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=_checkpointer,
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


def run_query(question: str, db: Session = None, thread_id: str = "default") -> dict:
    """执行一次自然语言数据查询。

    Args:
        question: 用户当前问题
        db: 数据库会话（保留兼容）
        thread_id: LangGraph 会话标识。同一 thread_id 自动恢复对话上下文。
    """
    import re

    logger.info(f"LangChain Agent 查询: {question[:100]} (thread={thread_id[:12]})")

    config = {"configurable": {"thread_id": thread_id}}
    try:
        agent = create_analysis_agent()
        result = agent.invoke(
            {"messages": [HumanMessage(content=question)]},
            config=config,
        )
    except Exception as e:
        logger.error(f"Agent 执行失败: {e}")
        return {
            "question": question,
            "answer": "查询服务暂时不可用，请稍后重试。",
            "iterations": 0,
            "tools_used": [],
        }

    messages = result.get("messages", [])
    answer = ""
    tools_used = []

    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tools_used.append(tc.get("name", "unknown"))
        if hasattr(msg, "content") and msg.content and not hasattr(msg, "tool_calls"):
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

    return {
        "question": question,
        "answer": answer,
        "iterations": iterations,
        "tools_used": tools_used,
    }


async def run_query_stream(question: str, thread_id: str = "default"):
    """流式执行 Agent 查询，yield SSE 事件字典。

    Args:
        question: 用户当前问题
        thread_id: LangGraph 会话标识。同一 thread_id 自动恢复对话上下文。
    """
    logger.info(f"Agent 流式查询: {question[:100]} (thread={thread_id[:12]})")

    config = {"configurable": {"thread_id": thread_id}}

    try:
        agent = create_analysis_agent()

        yield {"event": "agent_start", "data": {"question": question}}

        final_answer = ""
        tools_used = []

        async for chunk in agent.astream(
            {"messages": [HumanMessage(content=question)]},
            config=config,
            stream_mode="updates",
        ):
            for node_name, node_output in chunk.items():
                if node_name == "agent":
                    msgs = node_output.get("messages", [])
                    for msg in msgs:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tool_name = tc.get("name", "unknown")
                                tools_used.append(tool_name)
                                yield {
                                    "event": "tool_call",
                                    "data": {
                                        "tool": tool_name,
                                        "args": tc.get("args", {}),
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
            },
        }

    except Exception as e:
        logger.error(f"Agent 流式执行失败: {e}")
        yield {
            "event": "error",
            "data": {"message": "查询服务暂时不可用，请稍后重试。"},
        }

    yield {"event": "done", "data": {}}

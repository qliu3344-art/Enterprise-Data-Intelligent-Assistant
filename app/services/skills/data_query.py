"""data_query Skill：查询结构化数据库数据（考勤/销售/客户/运营）。"""

from app.services.skills.base import SkillResult


async def _collect_agent(
    question: str, thread_id: str, intent: str
) -> tuple[str, int, list, list]:
    """遍历 Agent 流式事件，收集最终答案 / 迭代轮数 / 工具列表 / 工具调用明细。"""
    from app.services.query_agent import run_query_stream

    answer = ""
    iterations = 0
    tools_used: list = []
    tool_calls: list = []
    async for evt in run_query_stream(question, thread_id, intent=intent):
        if evt["event"] == "answer":
            answer = evt["data"].get("content", "")
            iterations = evt["data"].get("iterations", 0)
            tools_used = evt["data"].get("tools_used", [])
            tool_calls = evt["data"].get("tool_calls", [])
        elif evt["event"] == "error":
            answer = evt["data"].get("message", "查询服务暂时不可用")
    return answer, iterations, tools_used, tool_calls


async def data_query_handler(question: str, thread_id: str = "default") -> SkillResult:
    """执行数据查询：走 LangChain Agent（3 个数据库工具）。"""
    answer, iterations, tools_used, tool_calls = await _collect_agent(
        question, thread_id, "data_query"
    )
    return SkillResult(
        answer=answer,
        mode="agent",
        iterations=iterations,
        tools_used=tools_used,
        tool_calls=tool_calls,
    )

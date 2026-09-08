"""MCP Server — 把企业数据智能助手的数据查询能力暴露为标准 MCP 工具。

对外出口：任何支持 MCP 的外部 Agent（Claude Code / Claude Desktop 等）都能
即插即用地调用本平台的三个数据查询工具。

与 Skill 化的关系：Skill 化是能力在项目内模块化，MCP Server 是同一套能力的
标准化对外出口。查询逻辑统一在 app/services/data_query_ops.py，被两种协议复用：
  - LangChain @tool（query_agent.py，对内给自有 Agent）
  - MCP @mcp.tool（本文件，对外给任意 MCP 客户端）

启动（stdio）：
    python -m app.mcp_server

Claude Code 接入：
    claude mcp add qy -- python -m app.mcp_server
"""

from mcp.server.fastmcp import FastMCP

from app.services import data_query_ops

mcp = FastMCP("企业数据智能助手")


@mcp.tool()
def query_cleaned_records(
    data_type: str = "",
    department: str = "",
    date_start: str = "",
    date_end: str = "",
    limit: int = 20,
) -> str:
    """查询清洗后的标准化数据记录。可筛选数据类型、部门、日期范围。"""
    return data_query_ops.query_cleaned_records(
        data_type, department, date_start, date_end, limit
    )


@mcp.tool()
def get_summary_stats(data_type: str = "") -> str:
    """获取汇总统计数据。返回各类型的记录数、异常率、平均质量分。"""
    return data_query_ops.get_summary_stats(data_type)


@mcp.tool()
def get_anomaly_details(
    data_type: str = "",
    department: str = "",
    employee_name: str = "",
    limit: int = 10,
) -> str:
    """查询异常记录的详细信息，包括异常原因和业务数据。

    可按数据类型、部门、员工姓名筛选。查具体人时用 employee_name 参数。
    """
    return data_query_ops.get_anomaly_details(
        data_type, department, employee_name, limit
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")

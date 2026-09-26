"""查询 Trace 表 — 记录每次自然语言查询的执行信息（可观测性基础）。

回放归回放：会话层记 session_id / role / content（见 chat_history）；
归因归这里：意图、工具、参数、分段耗时、路由分布。回放和归因是两个需求，
不要塞进一张表。
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, Float, DateTime, JSON, Index

from app.database import Base


class QueryTrace(Base):
    __tablename__ = "query_trace"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 从 API 入口生成，一路透传，出问题时一个 trace_id 捞出全链路
    trace_id = Column(String(36), default="", comment="全链路追踪标识")
    session_id = Column(String(64), nullable=False, comment="会话标识")
    question = Column(Text, nullable=False, comment="用户问题")
    answer = Column(Text, nullable=False, comment="最终答案")
    intent = Column(String(32), default="", comment="意图分类 data_query/doc_query/hybrid")
    mode = Column(String(32), default="", comment="执行模式 agent/rag/hybrid")
    iterations = Column(Integer, default=0, comment="工具调用次数（Agent 迭代轮数）")
    tools_used = Column(JSON, default=list, comment="实际调用的工具列表")
    # tools_used 只能告诉你调了哪个工具；tool_calls 里的 args 才能告诉你参数填对没有。
    # get_anomaly_details(employee_name="郑十") 和 get_anomaly_details(department="销售部")
    # 是同一个工具名、完全不同的查询。只核对工具名，会出现「工具名对、参数全错」照样满分。
    tool_calls = Column(JSON, default=list, comment="工具名 + 实际参数（含 args）")
    # 总耗时 3 秒说明不了任何事。拆成意图/检索/Agent/融合四段，才能一眼看出是检索慢，
    # 还是 Agent 多跑了一轮。
    stage_timings = Column(JSON, default=dict, comment="分段耗时（毫秒）：intent/retrieval/agent/fusion")
    # 意图路由的分布派生量 —— 支持离线重新调阈值，不用重新标注
    route_path = Column(String(16), default="", comment="意图路由路径 logprobs/rule")
    route_degrade = Column(String(64), default="", comment="路由失败原因分类，正常为空")
    p_top = Column(Float, default=0.0, comment="模型选中该标签的真实概率")
    label_mass = Column(Float, default=0.0, comment="三个合法标签概率之和（prompt 约束健康度）")
    route_margin = Column(Float, default=0.0, comment="top1 与 top2 的概率差")
    top_logprobs = Column(JSON, default=dict, comment="首 token 的 top5 原始分布")
    latency_ms = Column(Float, default=0.0, comment="端到端耗时（毫秒）")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    __table_args__ = (
        Index("idx_trace_session", "session_id", "created_at"),
        Index("idx_trace_trace_id", "trace_id"),
    )

"""Skill 抽象：把 Agent 能力模块化为可复用的技能单元。

每个 Skill = 自描述元数据（name / description / instructions）+ 执行入口（handler）。
元数据与操作手册来自 definitions/<name>/SKILL.md，由 loader 解析、registry 绑定 handler；
意图路由（intent_router）输出的 intent 值就是 skill 的 name，通过 registry 按需加载。
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class SkillResult:
    """Skill 执行后的标准化返回结构。"""

    answer: str
    mode: str
    iterations: int = 0
    tools_used: list = field(default_factory=list)
    # 工具名 + 实际参数。tools_used 只能说明调了哪个工具，参数对不对要看这里
    tool_calls: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # sources / chunks_count / rag_data 等


@dataclass
class Skill:
    """一个可复用的 Agent 能力单元。"""

    name: str  # 唯一标识，= intent 值（data_query / doc_query / hybrid）
    description: str  # 自描述：触发条件（SKILL.md frontmatter）
    instructions: str  # 操作手册：Agent 执行时按需注入（doc_query 不经 Agent，为空串）
    handler: Callable[..., Awaitable[SkillResult]]  # 执行函数
    injects_instructions: bool = True  # 该 Skill 是否需要把手册注入 Agent
    extends: str | None = None  # 手册复用自哪个 Skill（hybrid 复用 data_query）

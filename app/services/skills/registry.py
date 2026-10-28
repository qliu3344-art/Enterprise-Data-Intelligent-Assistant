"""Skill 注册表：扫描 definitions/ 下的 SKILL.md，按 name 绑定 handler。

定义在文件里（SKILL.md：元数据 + 操作手册），执行入口在代码里（handler）。
这个分工是刻意的——手册是知识，会被业务反复改；handler 是逻辑，要过测试和 review。

新增一类能力 = 加一个 <name>/SKILL.md + 在 HANDLERS 里绑一个 handler，路由层不用动。

⚠️ import 约定（改动前先看这里）：
  - handlers（data_query / doc_query / hybrid）不得在模块顶层 import query_agent，
    现在都是在函数体内延迟 import；
  - query_agent 也不得在顶层 import 本模块。
  否则 registry -> handler -> query_agent -> registry 在导入阶段成环。
"""

from app.logger import logger
from app.services.skills.base import Skill
from app.services.skills.data_query import data_query_handler
from app.services.skills.doc_query import doc_query_handler
from app.services.skills.hybrid import hybrid_handler
from app.services.skills.loader import (
    SkillDefinition,
    SkillLoadError,
    load_skill_definitions,
)

# handler 是代码，必须显式绑定：有定义没 handler、有 handler 没定义，启动就报错，
# 避免「SKILL.md 删了但 Python 里还留着幽灵实现」这种最难查的不一致。
HANDLERS = {
    "data_query": data_query_handler,
    "doc_query": doc_query_handler,
    "hybrid": hybrid_handler,
}

# 未知 intent 的回退目标。它是意图路由之外的第三道安全网，必须存在。
FALLBACK_SKILL = "data_query"

_SKILLS: dict[str, Skill] | None = None


def _resolve(
    defs: dict[str, SkillDefinition], handlers: dict
) -> dict[str, Skill]:
    """把定义和 handler 合成 Skill，并解析 extends 继承关系。"""
    missing = sorted(set(defs) - set(handlers))
    orphan = sorted(set(handlers) - set(defs))
    if missing:
        raise SkillLoadError(f"以下 Skill 没有绑定 handler: {missing}")
    if orphan:
        raise SkillLoadError(f"以下 handler 没有对应的 SKILL.md 目录: {orphan}")
    if FALLBACK_SKILL not in defs:
        raise SkillLoadError(f"缺少 {FALLBACK_SKILL!r} 定义：未知意图回退依赖它")

    resolved: dict[str, Skill] = {}

    def build(name: str, stack: tuple[str, ...] = ()) -> Skill:
        if name in resolved:
            return resolved[name]
        if name in stack:
            raise SkillLoadError("extends 成环: " + " -> ".join(stack + (name,)))
        if name not in defs:
            raise SkillLoadError(f"extends 指向不存在的 Skill: {name!r}")

        d = defs[name]
        if d.extends:
            base = build(d.extends, stack + (name,))
            if not base.injects_instructions:
                raise SkillLoadError(
                    f"{d.source}: extends 的 {d.extends!r} 本身不注入手册"
                )
            instructions = base.instructions  # 同一个字符串对象，不复制文本
        else:
            instructions = d.instructions

        skill = Skill(
            name=d.name,
            description=d.description,
            instructions=instructions,
            handler=handlers[name],
            injects_instructions=d.injects_instructions,
            extends=d.extends,
        )
        resolved[name] = skill
        return skill

    return {name: build(name) for name in sorted(defs)}


def load_skills(*, force: bool = False, skills_dir=None) -> dict[str, Skill]:
    """加载并缓存 Skill 注册表（幂等）。

    启动时显式调用、让配置错误在 startup 阶段就暴露；请求路径经 get_skill 惰性兜底，
    这样 eval.py 和自检脚本不依赖 FastAPI 的 startup 也能拿到同一份定义。
    """
    global _SKILLS
    if _SKILLS is not None and not force:
        return _SKILLS

    defs = load_skill_definitions(skills_dir)
    skills = _resolve(defs, HANDLERS)
    _SKILLS = skills  # 先建好整张表再整体替换，读侧不会看到半成品

    logger.info(
        "Skill 定义加载完成: "
        + ", ".join(
            f"{s.name}({len(s.instructions)}字"
            + (f", extends={s.extends}" if s.extends else "")
            + ")"
            for s in skills.values()
        )
    )
    return skills


def get_skill(intent: str) -> Skill:
    """按 intent 取 Skill，未知意图回退到 data_query（安全网）。"""
    skills = load_skills()
    return skills.get(intent, skills[FALLBACK_SKILL])


def all_skills() -> list[Skill]:
    """按名称排序返回全部 Skill，供 /skills 之类的自描述出口使用。"""
    skills = load_skills()
    return [skills[name] for name in sorted(skills)]

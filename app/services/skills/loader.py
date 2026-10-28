"""Skill 定义加载：从 definitions/<name>/SKILL.md 解析元数据与操作手册。

SKILL.md 是一份能力定义的唯一真相——frontmatter 放自描述元数据（name / description），
正文就是按需注入给 Agent 的操作手册。执行入口（handler）是代码，写不成文件，
由 registry 按 name 绑定，两条来源在 registry 里汇合。

校验一律抛错、不做部分加载：SKILL.md 决定意图对应哪份手册，属于正确性配置，
和意图标签校验同属「宁可服务起不来，也不能带着错配置上线」那一类。
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.logger import logger

# 目录相对定位，不依赖 CWD：本地、Docker（/app/app/services/skills/definitions）、
# 以及从别的目录启动的 eval.py / check_*.py 都解析到同一处。
SKILLS_DIR = Path(__file__).resolve().parent / "definitions"

# 与意图标签同名，所以是小写 + 下划线（data_query 而不是 data-query）
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_DESCRIPTION_CHARS = 1024

# 本文件认识的全部 frontmatter 字段；其余字段只告警不拦截（拼写错误能被发现）
KNOWN_KEYS = {"name", "description", "inject", "extends"}
FENCE = "---"


class SkillLoadError(RuntimeError):
    """SKILL.md 缺失、格式非法，或注册表约束不满足。启动期直接向上抛。"""


@dataclass(frozen=True)
class SkillDefinition:
    """一个 SKILL.md 解析出来的结果（尚未绑定 handler）。"""

    name: str
    description: str
    instructions: str  # 正文；声明 extends 时为空串，由 registry 继承
    injects_instructions: bool
    extends: str | None
    source: Path


def parse_skill_text(
    text: str, *, dir_name: str, source: str = "<memory>"
) -> SkillDefinition:
    """解析一份 SKILL.md 文本（纯函数，便于自检脚本直接构造非法输入）。"""
    text = text.lstrip("﻿")
    lines = text.splitlines()

    if not lines or lines[0].strip() != FENCE:
        raise SkillLoadError(f"{source}: 缺少 YAML frontmatter（首行必须是 ---）")

    close = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == FENCE), None
    )
    if close is None:
        raise SkillLoadError(f"{source}: frontmatter 未闭合（缺少第二个 ---）")

    try:
        meta = yaml.safe_load("\n".join(lines[1:close]))
    except yaml.YAMLError as e:
        raise SkillLoadError(f"{source}: frontmatter 不是合法 YAML: {e}") from e
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise SkillLoadError(f"{source}: frontmatter 必须是键值映射")

    body = "\n".join(lines[close + 1 :]).strip()

    unknown = sorted(set(meta) - KNOWN_KEYS)
    if unknown:
        logger.warning(f"{source}: 未识别的 frontmatter 字段 {unknown}，已忽略")

    name = meta.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SkillLoadError(
            f"{source}: name 缺失或格式非法（要求 ^[a-z][a-z0-9_]{{0,63}}$）: {name!r}"
        )
    if name != dir_name:
        raise SkillLoadError(f"{source}: name={name!r} 与所在目录名 {dir_name!r} 不一致")

    description = meta.get("description")
    if not isinstance(description, str):
        raise SkillLoadError(
            f"{source}: description 缺失或不是字符串（含英文冒号时请用引号包起来）"
        )
    description = " ".join(description.split())
    if not description:
        raise SkillLoadError(f"{source}: description 不能为空")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillLoadError(
            f"{source}: description 超过 {MAX_DESCRIPTION_CHARS} 字符（当前 {len(description)}）"
        )

    extends = meta.get("extends")
    if extends is not None:
        if not isinstance(extends, str) or not NAME_RE.match(extends):
            raise SkillLoadError(f"{source}: extends 指向的名称不合法: {extends!r}")
        if extends == name:
            raise SkillLoadError(f"{source}: extends 不能指向自身")
        if body:
            raise SkillLoadError(
                f"{source}: 声明了 extends: {extends}，正文必须留空（手册由被继承的 Skill 提供）"
            )

    inject = meta.get("inject", True)
    if not isinstance(inject, bool):
        raise SkillLoadError(f"{source}: inject 只能是 true / false，当前 {inject!r}")
    if not inject:
        if body:
            raise SkillLoadError(f"{source}: inject: false 时正文必须留空")
        if extends is not None:
            raise SkillLoadError(f"{source}: inject: false 与 extends 语义冲突，二选一")
    elif not body and extends is None:
        raise SkillLoadError(
            f"{source}: 正文为空。不需要操作手册请写 inject: false；复用别人的手册请写 extends"
        )

    return SkillDefinition(
        name=name,
        description=description,
        instructions=body,
        injects_instructions=inject,
        extends=extends,
        source=Path(source),
    )


def load_skill_definitions(skills_dir=None) -> dict[str, SkillDefinition]:
    """扫描 definitions/ 下每个技能目录，返回 name -> 定义。任何非法定义直接抛错。"""
    root = Path(skills_dir) if skills_dir is not None else SKILLS_DIR
    if not root.is_dir():
        raise SkillLoadError(f"Skill 定义目录不存在: {root}")

    defs: dict[str, SkillDefinition] = {}
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name.startswith((".", "__")):
            continue
        md = entry / "SKILL.md"
        if not md.is_file():
            raise SkillLoadError(f"{entry}: 技能目录缺少 SKILL.md")
        text = md.read_text(encoding="utf-8-sig")
        d = parse_skill_text(text, dir_name=entry.name, source=str(md))
        if d.name in defs:
            raise SkillLoadError(f"{md}: name {d.name!r} 与 {defs[d.name].source} 重复")
        defs[d.name] = d

    if not defs:
        raise SkillLoadError(f"{root}: 没有找到任何 Skill 定义")
    return defs

"""Skill 定义自检 — SKILL.md 文件、注册表约束与操作手册完整性。

SKILL.md 现在是操作手册的唯一来源，改坏它不会报错，只会让 Agent 悄悄失去指令。
这个脚本把「文件对不对」和「注册表绑得对不对」一次性钉死。

用法：
    python scripts/check_skills.py
    python scripts/check_skills.py --with-agent   # 额外验证注入链路（导入 Agent 约 30s）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.skills.loader import (  # noqa: E402
    SKILLS_DIR,
    SkillDefinition,
    SkillLoadError,
    load_skill_definitions,
    parse_skill_text,
)
from app.services.skills.registry import (  # noqa: E402
    HANDLERS,
    _resolve,
    all_skills,
    get_skill,
    load_skills,
)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}{'  ' + detail if detail else ''}")


def raises(name: str, fn, expect: str = ""):
    """断言 fn() 抛 SkillLoadError；给了 expect 还要断言报错原因对得上。

    只断言「抛了错」不够——校验是有顺序的，前置校验先炸会让后面的用例假通过。
    """
    try:
        fn()
    except SkillLoadError as e:
        msg = str(e)
        ok = expect in msg if expect else True
        check(name, ok, f"→ {msg[:60]}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"抛了别的异常 {type(e).__name__}: {e}")
    else:
        check(name, False, "没有抛错")


def md(frontmatter: str, body: str = "") -> str:
    return f"---\n{frontmatter.strip()}\n---\n\n{body}\n"


# —— 1. 定义文件本身 ——
print("\n[1] 定义文件 — definitions/<name>/SKILL.md")
check("definitions 目录存在", SKILLS_DIR.is_dir(), str(SKILLS_DIR))

dirs = sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith("__"))
check("恰好三个技能目录", dirs == ["data_query", "doc_query", "hybrid"], str(dirs))

defs = load_skill_definitions()
check("三个定义全部解析成功", sorted(defs) == ["data_query", "doc_query", "hybrid"], str(sorted(defs)))

for name, d in sorted(defs.items()):
    check(f"{name}.name 与目录名一致", d.name == name)
    check(f"{name}.description 非空且 ≤1024", 0 < len(d.description) <= 1024, f"{len(d.description)} 字")

# —— 2. 注册表与继承 ——
print("\n[2] 注册表 — handler 绑定与 extends 继承")
skills = load_skills(force=True)
check("注册表恰好三个", sorted(skills) == ["data_query", "doc_query", "hybrid"], str(sorted(skills)))
check("handler 与定义一一对应", set(HANDLERS) == set(skills))

for name in ("data_query", "doc_query", "hybrid"):
    check(f"{name} 绑到了正确的 handler", skills[name].handler is HANDLERS[name])

dq = skills["data_query"].instructions
check("data_query 手册非空", len(dq) > 0, f"{len(dq)} 字")
for tool in ("query_cleaned_records", "get_summary_stats", "get_anomaly_details"):
    check(f"data_query 手册含 {tool}", tool in dq)
check("data_query 手册含决策树", "工具选择决策树" in dq)
check("data_query 需要注入手册", skills["data_query"].injects_instructions)

check("doc_query 手册为空串", skills["doc_query"].instructions == "")
check("doc_query 不注入手册（决策，不是遗漏）", not skills["doc_query"].injects_instructions)

check("hybrid 声明 extends=data_query", skills["hybrid"].extends == "data_query")
check(
    "hybrid 复用的是同一份手册对象（不是复制的文本）",
    skills["hybrid"].instructions is skills["data_query"].instructions,
)

# —— 3. 调度行为 ——
print("\n[3] 调度 — 未知意图安全网")
check("未知 intent 回退到 data_query", get_skill("no_such_intent").name == "data_query")
check("回退后 handler 也是 data_query 的", get_skill("no_such_intent").handler is HANDLERS["data_query"])
check("all_skills 按名排序", [s.name for s in all_skills()] == ["data_query", "doc_query", "hybrid"])

# —— 4. frontmatter 负例（纯函数，不落盘）——
print("\n[4] frontmatter 负例 — 每个都必须抛 SkillLoadError")
OK = "name: demo\ndescription: 一个合法的示例技能"

raises("缺 frontmatter", lambda: parse_skill_text("# 没有 frontmatter", dir_name="demo"),
       expect="缺少 YAML frontmatter")
raises("frontmatter 未闭合", lambda: parse_skill_text("---\nname: demo\n正文", dir_name="demo"))
raises("YAML 语法错", lambda: parse_skill_text("---\nname: [demo\n---\n", dir_name="demo"))
raises("name 与目录名不一致", lambda: parse_skill_text(md(OK, "正文"), dir_name="other"),
       expect="与所在目录名")
raises(
    "name 含大写",
    lambda: parse_skill_text(md("name: Demo\ndescription: x", "正文"), dir_name="Demo"),
)
raises("描述缺失", lambda: parse_skill_text(md("name: demo", "正文"), dir_name="demo"))
raises("描述为空", lambda: parse_skill_text(md('name: demo\ndescription: "  "', "正文"), dir_name="demo"))
raises(
    "描述超长",
    lambda: parse_skill_text(md(f"name: demo\ndescription: {'字' * 1025}", "正文"), dir_name="demo"),
)
raises("需要注入但正文为空", lambda: parse_skill_text(md(OK, ""), dir_name="demo"),
       expect="正文为空")
raises(
    "inject: false 却写了正文",
    lambda: parse_skill_text(md(OK + "\ninject: false", "正文"), dir_name="demo"),
)
raises(
    "extends 与正文同时存在",
    lambda: parse_skill_text(md(OK + "\nextends: base", "正文"), dir_name="demo"),
)
raises(
    "extends 指向自身",
    lambda: parse_skill_text(md(OK + "\nextends: demo"), dir_name="demo"),
)
raises(
    "inject: false 与 extends 冲突",
    lambda: parse_skill_text(md(OK + "\ninject: false\nextends: base"), dir_name="demo"),
)

# —— 5. 跨文件约束 ——
print("\n[5] 跨文件约束 — extends 解析与 handler 覆盖")


def fake(name: str, instructions: str = "手册", extends=None, inject=True) -> SkillDefinition:
    return SkillDefinition(
        name=name,
        description="x",
        instructions=instructions,
        injects_instructions=inject,
        extends=extends,
        source=f"<{name}>",
    )


H = {k: v for k, v in HANDLERS.items()}

raises(
    "有定义没有 handler",
    lambda: _resolve({"data_query": fake("data_query"), "ghost": fake("ghost")}, H),
)
raises(
    "有 handler 没有定义",
    lambda: _resolve({"data_query": fake("data_query")}, H),
    expect="没有对应的 SKILL.md",
)
raises(
    "缺少 data_query（回退目标）",
    lambda: _resolve({"hybrid": fake("hybrid")}, {"hybrid": H["hybrid"]}),
)
_ALL = {
    "data_query": fake("data_query"),
    "doc_query": fake("doc_query", "", inject=False),
}
raises(
    "extends 指向不存在的 Skill",
    lambda: _resolve({**_ALL, "hybrid": fake("hybrid", "", extends="ghost")}, H),
    expect="extends 指向不存在",
)
raises(
    "extends 成环",
    lambda: _resolve(
        {
            "doc_query": fake("doc_query", "", inject=False),
            "data_query": fake("data_query", "", extends="hybrid"),
            "hybrid": fake("hybrid", "", extends="data_query"),
        },
        H,
    ),
    expect="extends 成环",
)
raises(
    "extends 的目标不注入手册",
    lambda: _resolve(
        {
            "data_query": fake("data_query"),
            "doc_query": fake("doc_query", "", inject=False),
            "hybrid": fake("hybrid", "", extends="doc_query"),
        },
        H,
    ),
)

# —— 6. 可选的注入链路（导入 Agent 很慢，默认不跑）——
print("\n[6] 注入链路")
if "--with-agent" in sys.argv:
    from app.services.query_agent import _load_skill_instructions  # noqa: E402

    d_inject = _load_skill_instructions("data_query")
    check("data_query 注入非空", d_inject != "")
    check("data_query 注入就是 SKILL.md 正文", d_inject == skills["data_query"].instructions)
    check(
        "hybrid 与 data_query 注入同一份手册",
        _load_skill_instructions("hybrid") == d_inject,
    )
    check("doc_query 不注入", _load_skill_instructions("doc_query") == "")
    check("未知 intent 不注入（回退只管调度，不带手册）", _load_skill_instructions("nope") == "")
else:
    print("  ⏭  跳过（加 --with-agent 运行，导入 query_agent 约 30s）")

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败：" + "、".join(FAIL))
    sys.exit(1)

# -*- coding: utf-8 -*-
"""父子块回填校验：命中条款在前、整章上下文在后，不丢不重。

用法：python scripts/check_parent_child.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.rag.retriever import retrieve  # noqa: E402
from app.services.rag.service import MAX_PARENT_CONTEXT_CHARS, _build_context  # noqa: E402
from app.services.rag.vector_store import fetch_parent_chunks  # noqa: E402

QUERIES = [
    "请假3天以上需要谁审批？",
    "单月请假超过5天会怎样？",
    "迟到超过多少分钟算迟到？",
    "大促期间网站访问量目标是多少，结束后怎么恢复？",
    "A级客户的合同金额标准是多少？",
]

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK   {name}{' — ' + detail if detail else ''}")
    else:
        FAIL += 1
        print(f"  FAIL {name}{' — ' + detail if detail else ''}")


def strip_chapter(text, chapter):
    prefix = f"## {chapter}\n\n"
    return text[len(prefix):] if chapter and text.startswith(prefix) else text


def main():
    print(f"父块回填上限: {MAX_PARENT_CONTEXT_CHARS} 字\n")

    for q in QUERIES:
        chunks = retrieve(q, top_k=5)
        context = _build_context(chunks)
        refs = {
            (c["metadata"].get("file_name", ""), c["metadata"].get("parent_key", ""))
            for c in chunks
        }
        parents = fetch_parent_chunks(refs)

        print(f"\n=== {q} ===")
        print(f"命中 {len(chunks)} 条，上下文 {len(context)} 字")
        for h in chunks:
            print(f"  - {h['metadata'].get('doc_title')} / "
                  f"{h['metadata'].get('chapter')} / {h['metadata'].get('clause_no')}")

        # 1. 每个命中条款正文只出现一次（整章上下文里排除了命中块）
        for c in chunks:
            body = strip_chapter(c["text"], c["metadata"].get("chapter", ""))
            occurrences = context.count(body)
            check(
                f"命中正文只出现一次: {c['metadata'].get('clause_no') or '章前言'}",
                occurrences == 1,
                f"{occurrences} 次",
            )

        # 2. 父块必须按 chunk_index 升序、覆盖整章
        for ref, items in parents.items():
            idx = [x["chunk_index"] for x in items]
            check(
                f"父块按序取回: {ref[0]} / {items[0]['metadata'].get('chapter')}",
                idx == sorted(idx) and len(items) > 0,
                f"{len(items)} 块",
            )

        # 3. 每个有同章兄弟块的命中章，各出现一次【本章上下文】
        groups: dict[tuple, list[dict]] = {}
        for c in chunks:
            meta = c["metadata"]
            groups.setdefault(
                (meta.get("file_name", ""), meta.get("parent_key", "")), []
            ).append(c)

        expected = 0
        for ref, hits in groups.items():
            hit_keys = {c["metadata"].get("chunk_key") for c in hits}
            siblings = [x for x in parents.get(ref, [])
                        if x["metadata"].get("chunk_key") not in hit_keys]
            if siblings:
                expected += 1
        check(
            "命中章都回填了本章上下文",
            context.count("【本章上下文】") == expected,
            f"期望 {expected} 处，实际 {context.count('【本章上下文】')} 处",
        )

        # 4. 整块上下文不能因为回填无限膨胀
        check(
            "总上下文长度受控",
            len(context) <= len(chunks) * MAX_PARENT_CONTEXT_CHARS + 500,
            f"{len(context)} 字",
        )

        first = context.split("\n\n---\n\n")[0]
        print("  ── 首个上下文块 ──")
        for line in first.split("\n")[:12]:
            print(f"  | {line}")

    # 5. 大章兜底：把上限压到 300 字，只保留命中条款的前后邻居
    import app.services.rag.service as svc

    chunks = retrieve("迟到超过多少分钟算迟到？", top_k=5)
    svc.MAX_PARENT_CONTEXT_CHARS = 300
    try:
        capped = svc._build_context(chunks)
    finally:
        svc.MAX_PARENT_CONTEXT_CHARS = MAX_PARENT_CONTEXT_CHARS
    first = capped.split("\n\n---\n\n")[0]
    check("超长章退化为前后邻居", "### 第五条" in first, "保留了前一条")
    check("超长章不再带更远的条", "### 第四条" not in first, "排除了前两条")

    print(f"\n结果: {PASS} 通过, {FAIL} 失败\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

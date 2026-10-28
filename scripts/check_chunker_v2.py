# -*- coding: utf-8 -*-
"""条款级分块校验：不跨章、不丢条、key 稳定唯一。

用法：python scripts/check_chunker_v2.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.rag.chunker import (  # noqa: E402
    CHUNKER_VERSION,
    MAX_CHUNK_SIZE,
    _split_by_headings,
    chunk_all,
)
from app.services.rag.document_loader import load_all_documents  # noqa: E402

PASS, FAIL = 0, 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  OK   {name}{' — ' + detail if detail else ''}")
    else:
        FAIL += 1
        print(f"  FAIL {name}{' — ' + detail if detail else ''}")


def main():
    docs = load_all_documents()
    chunks = chunk_all(docs)
    print(f"\n分块器版本: {CHUNKER_VERSION}")
    print(f"文档 {len(docs)} 份 → {len(chunks)} chunks\n")

    check("chunk_key 全部非空", all(c.chunk_key for c in chunks))
    check(
        "同一文档内 chunk_key 唯一",
        len({(c.file_name, c.chunk_key) for c in chunks}) == len(chunks),
    )
    check(
        "chunk 不跨章",
        all(sum(1 for ln in c.text.split("\n") if ln.startswith("## ")) <= 1
            for c in chunks),
    )
    check(
        "条款 chunk 都有 clause_no",
        all(c.clause_no for c in chunks if c.chunk_key.startswith("clause:")),
    )

    # 每个源条款的正文首 12 字，必须出现在且只出现在它自己的 chunk 里。
    # 这条是 2026-09-28 那个「每章最后一条正文静默丢失」bug 的回归检查。
    missing = []
    lost = []
    duplicated = []
    long_clauses = []
    clause_total = 0

    for doc in docs:
        for chapter in _split_by_headings(doc.content):
            for clause in chapter["clauses"]:
                if not clause["heading"]:
                    continue
                clause_total += 1
                heading = clause["heading"]
                chunk = next(
                    (c for c in chunks
                     if c.file_name == doc.file_name
                     and c.chunk_key.startswith(f"clause:{heading}")),
                    None,
                )
                if chunk is None:
                    missing.append((doc.file_name, heading))
                    continue
                if len(chunk.text) > MAX_CHUNK_SIZE:
                    long_clauses.append((doc.file_name, heading, len(chunk.text)))

                body = clause["body"].strip()
                if not body:
                    continue
                if body[:12] not in chunk.text:
                    lost.append((doc.file_name, heading, body[:12]))

                # 去重检查用完整正文，不用前 12 字：条款开头常是
                # 「1. **定义**：月度」这种模板句，短探针会误报。
                if len(body) >= 20:
                    occurrences = sum(
                        1 for c in chunks
                        if c.file_name == doc.file_name and body in c.text
                    )
                    if occurrences > 1:
                        duplicated.append(
                            (doc.file_name, heading, len(body), occurrences)
                        )

    check("每条条款都有对应 chunk", not missing,
          f"缺 {len(missing)} 条" if missing else f"{clause_total} 条")
    check("条款正文没有静默丢失", not lost,
          f"丢 {len(lost)} 条" if lost else f"{clause_total} 条")
    check("同一条正文没有被切到多个 chunk", not duplicated,
          f"重复 {len(duplicated)} 处" if duplicated else "")

    print("\n--- 每份文档 ---")
    by_doc = {}
    for c in chunks:
        by_doc.setdefault(c.file_name, []).append(c)
    for doc in docs:
        cs = by_doc.get(doc.file_name, [])
        print(
            f"  {doc.file_name}: {len(cs)} chunks | "
            f"{min((len(c.text) for c in cs), default=0)}~"
            f"{max((len(c.text) for c in cs), default=0)} 字"
        )

    if long_clauses:
        print(f"\n超过 {MAX_CHUNK_SIZE} 字的条款（只告警、不拆条）:")
        for doc_name, heading, size in long_clauses:
            print(f"  {doc_name} / {heading}: {size} 字")

    second = chunk_all(docs)
    check(
        "分块结果可复现",
        [(c.file_name, c.chunk_key, c.text) for c in chunks]
        == [(c.file_name, c.chunk_key, c.text) for c in second],
    )

    print(f"\n结果: {PASS} 通过, {FAIL} 失败\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

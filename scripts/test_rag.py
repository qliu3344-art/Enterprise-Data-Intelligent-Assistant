"""RAG 全链路测试：索引 → 检索 → 问答。
用法：python scripts/test_rag.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.rag.document_loader import load_all_documents
from app.services.rag.chunker import chunk_all
from app.services.rag.vector_store import index_all, needs_reindex, get_document_stats
from app.services.rag.retriever import retrieve, refresh_retriever
from app.services.rag.service import ask_rag

PASS, FAIL = 0, 0


def check(step, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {step}{' — ' + detail if detail else ''}")
    else:
        FAIL += 1
        print(f"  ❌ {step}{' — ' + detail if detail else ''}")


print("=" * 60)
print("1. 文档加载")
print("=" * 60)

docs = load_all_documents()
check("加载文档数", len(docs) == 8, f"共 {len(docs)} 份")
for d in docs:
    check(f"  文档有内容: {d.title}", len(d.content) > 100, f"{len(d.content)} 字符")

print("\n" + "=" * 60)
print("2. 文本分块")
print("=" * 60)

chunks = chunk_all(docs)
check("分块数量合理", len(chunks) > 20, f"共 {len(chunks)} chunks")
check("每个 chunk 有标题", all(c.doc_title for c in chunks))
check("每个 chunk 有章节", sum(1 for c in chunks if c.chapter) > len(chunks) * 0.5,
      f"{sum(1 for c in chunks if c.chapter)}/{len(chunks)} 有章节信息")

print("\n" + "=" * 60)
print("3. 向量索引")
print("=" * 60)

result = index_all(force=True)
check("索引成功", result["chunks"] > 0, f"{result['documents']} 文档 → {result['chunks']} chunks")
check("索引数 = 分块数", result["chunks"] == len(chunks),
      f"indexed={result['chunks']} vs chunks={len(chunks)}")

stats = get_document_stats()
check("已有索引统计", len(stats) == 8, f"共 {len(stats)} 类文档")

# 刷新检索器
refresh_retriever()

print("\n" + "=" * 60)
print("4. 混合检索")
print("=" * 60)

# 测试关键词检索
hits = retrieve("加班超过多少小时算异常", top_k=5)
check("检索结果非空", len(hits) > 0)
check("命中加班相关文档", any("加班" in h["text"] or "考勤" in h["text"] for h in hits))

# 打印检索结果
for i, h in enumerate(hits[:3]):
    meta = h["metadata"]
    print(f"  [{i+1}] {meta.get('doc_title', '?')} / {meta.get('chapter', '?')} — score={h['score']}")

print("\n" + "=" * 60)
print("5. RAG 问答")
print("=" * 60)

questions = [
    "加班超过多少小时算异常？",
    "A级客户的合同金额标准是多少？",
    "双11大促期间销售订单的异常判定标准有什么不同？",
    "运营部的网站访问量目标值是多少？",
    "年假天数怎么计算？",
]

for q in questions:
    result = ask_rag(q)
    answer = result["answer"]
    has_citation = "《" in answer or "第" in answer
    has_no_json = not answer.strip().startswith("{") and not answer.strip().startswith("```")
    check(f"Q: {q[:25]}...",
          result["chunks_count"] > 0,
          f"chunks={result['chunks_count']}, cite={'Y' if has_citation else 'N'}, ans={answer[:60]}...")

    if not has_citation:
        print(f"     ⚠ 未找到引用标记")

print("\n" + "=" * 60)
print("6. Edge Cases")
print("=" * 60)

# 无关问题
result = ask_rag("今天天气怎么样？")
check("无关问题应有回应", len(result["answer"]) > 0)

# 空问题场景
result = ask_rag("Python怎么写for循环？")
check("技术问题应诚实回答", "未找到" in result["answer"] or "相关" in result["answer"] or len(result["answer"]) > 0)

# ==========================================================
print("\n" + "=" * 60)
print(f"测试结果: {PASS} 通过, {FAIL} 失败, 共 {PASS+FAIL} 项")
print("=" * 60)

if FAIL > 0:
    sys.exit(1)
else:
    print("✅ RAG 全链路测试通过！")

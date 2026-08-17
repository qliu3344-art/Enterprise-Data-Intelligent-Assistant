"""回填 eval_dataset_expanded.json 中缺失的 expected_doc_keywords。

逻辑：
1. 为 3 个缺少 keywords 的 original 条目自动提取关键词
2. variant 从对应 original 继承 expected_doc_keywords

用法:
    python scripts/backfill_keywords.py
"""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = PROJECT_ROOT / "eval_dataset_expanded.json"

# —— 需要手动补充 keywords 的 original 条目（在原 20 条中的 id） ——
# 这些是 hybrid 意图，RAG 部分需要评估但原数据集缺了 keywords
MISSING_ORIGINAL_KEYWORDS = {
    2: ["郑十", "异常"],           # 郑十为什么被标记为异常？
    13: ["销售", "合同", "异常"],   # 销售部合同金额为什么被判定为异常？
    18: ["销售", "异常率"],         # 销售部为什么异常率比其他部门高？
}


def backfill():
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Step 1: 建立 original source_id → keywords 的映射
    # source 字段格式: "original_N" 或 "variant_of_N"，N 是原 20 条中的 id
    original_keywords = {}  # source_id → keywords list

    for item in data:
        if item["source"].startswith("original_"):
            source_id = int(item["source"].replace("original_", ""))
            if item.get("expected_doc_keywords"):
                original_keywords[source_id] = item["expected_doc_keywords"]
            elif source_id in MISSING_ORIGINAL_KEYWORDS:
                # 用预设的关键词
                original_keywords[source_id] = MISSING_ORIGINAL_KEYWORDS[source_id]

    print(f"已有 keywords 的 original 条目: {len(original_keywords)}")
    for sid, kws in sorted(original_keywords.items()):
        print(f"  source_id={sid}: {kws}")

    # Step 2: 回填
    filled_originals = 0
    filled_variants = 0

    for item in data:
        # 只处理 doc_query 和 hybrid
        if item.get("expected_intent") not in ("doc_query", "hybrid"):
            continue
        if item.get("expected_doc_keywords"):
            continue  # 已经有了

        if item["source"].startswith("original_"):
            source_id = int(item["source"].replace("original_", ""))
            if source_id in original_keywords:
                item["expected_doc_keywords"] = original_keywords[source_id]
                filled_originals += 1
                print(f"  ✅ original id={item['id']} (source_id={source_id}) ← {original_keywords[source_id]}")

        elif item["source"].startswith("variant_of_"):
            orig_id = int(item["source"].replace("variant_of_", ""))
            if orig_id in original_keywords:
                item["expected_doc_keywords"] = original_keywords[orig_id]
                filled_variants += 1
                # 只打印前几个
                if filled_variants <= 5:
                    print(f"  ✅ variant id={item['id']} (from source_id={orig_id}) ← {original_keywords[orig_id]}")

    print(f"\n回填完成: {filled_originals} 个 original + {filled_variants} 个 variant")

    # Step 3: 验证
    missing = [d for d in data
               if d.get("expected_intent") in ("doc_query", "hybrid")
               and not d.get("expected_doc_keywords")]
    if missing:
        print(f"\n⚠️ 仍有 {len(missing)} 条缺失:")
        for m in missing:
            print(f"  id={m['id']} source={m['source']}: {m['question'][:60]}")
    else:
        print(f"\n✅ 全部 {sum(1 for d in data if d.get('expected_intent') in ('doc_query', 'hybrid'))} 条 doc_query/hybrid 都有 keywords 了")

    # 保存
    with open(DATASET_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已保存: {DATASET_PATH}")


if __name__ == "__main__":
    backfill()

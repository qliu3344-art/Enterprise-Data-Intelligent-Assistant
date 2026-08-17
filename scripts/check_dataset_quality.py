"""评估数据集质检脚本。
检查自动生成的变体是否存在：语义漂移、重复、过短等问题。

用法:
    python scripts/check_dataset_quality.py
    python scripts/check_dataset_quality.py --dataset eval_dataset_expanded.json
    python scripts/check_dataset_quality.py --fix  # 自动修复可修复的问题
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def check_quality(dataset_path: str, auto_fix: bool = False):
    """检查数据集质量。"""
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    issues = []
    duplicates = set()

    print(f"数据集: {dataset_path}")
    print(f"条目数: {len(data)}\n")
    print("=" * 60)

    # 1. 检查 id 连续性
    for i, item in enumerate(data, 1):
        if item["id"] != i:
            issues.append(f"[id_gap] id={item['id']} 不在预期位置 {i}")
    if not any("id_gap" in s for s in issues):
        print("✅ id 连续性: 正常")

    # 2. 检查重复问题
    seen_questions = {}
    for item in data:
        q = item["question"].strip()
        q_normalized = q.rstrip("？?")  # 去掉末尾问号做宽松比较
        if q_normalized in seen_questions:
            other_id = seen_questions[q_normalized]
            duplicates.add(item["id"])
            issues.append(f"[duplicate] id={item['id']} \"{q[:60]}\" 与 id={other_id} 重复")
        else:
            seen_questions[q_normalized] = item["id"]

    if not duplicates:
        print("✅ 重复检查: 无重复")
    else:
        print(f"⚠️ 重复检查: {len(duplicates)} 条重复")

    # 3. 检查过短 / 过长
    for item in data:
        q = item["question"]
        if len(q) < 3:
            issues.append(f"[too_short] id={item['id']} 问题过短({len(q)}字): \"{q}\"")
        if len(q) > 200:
            issues.append(f"[too_long] id={item['id']} 问题过长({len(q)}字): \"{q[:60]}...\"")

    # 4. 检查变体语义漂移 — 关键词缺失
    #    原始问题的核心实体应在其变体中也出现
    original_keywords = {}
    for item in data:
        if item["source"].startswith("original_"):
            original_keywords[item["id"]] = {
                "question": item["question"],
                "keywords": _extract_keywords(item["question"]),
                "category": item.get("category", ""),
                "intent": item.get("expected_intent", ""),
            }

    drift_count = 0
    for item in data:
        if item["source"].startswith("variant_of_"):
            orig_id = int(item["source"].replace("variant_of_", ""))
            # 找到对应的原始条目
            orig_info = None
            for oid, oinfo in original_keywords.items():
                oid_map = {}
                for it in data:
                    if it["source"] == f"original_{orig_id}":
                        oid_map[oid] = orig_id
                        break
            # 简化：通过 source 中的原始 id 找到对应的 original 条目
            orig_items = [it for it in data if it["source"] == f"original_{orig_id}"]
            if not orig_items:
                continue
            orig_q = orig_items[0]["question"]
            orig_kw = _extract_keywords(orig_q)

            variant_q = item["question"]
            variant_kw = _extract_keywords(variant_q)

            # 检查核心实体（数字、专有名词）是否保留
            if orig_kw["numbers"]:
                for num in orig_kw["numbers"]:
                    if num not in variant_q:
                        drift_count += 1
                        issues.append(
                            f"[drift] id={item['id']} 数字 '{num}' 丢失: "
                            f"原始=\"{orig_q[:50]}\" → 变体=\"{variant_q[:50]}\""
                        )
                        break
                else:
                    continue
                continue

            if orig_kw["entities"]:
                entity_hits = sum(1 for e in orig_kw["entities"] if e in variant_q)
                if entity_hits < max(1, len(orig_kw["entities"]) * 0.5):
                    drift_count += 1
                    issues.append(
                        f"[drift] id={item['id']} 实体丢失({entity_hits}/{len(orig_kw['entities'])}): "
                        f"原始=\"{orig_q[:50]}\" → 变体=\"{variant_q[:50]}\""
                    )

    if drift_count == 0:
        print("✅ 语义漂移: 未检测到")
    else:
        print(f"⚠️ 语义漂移: {drift_count} 条可疑")

    # 5. 统计报告
    print()
    print("=" * 60)
    print("统计报告:")
    print(f"  总条目: {len(data)}")
    print(f"  问题数: {len(issues)}")
    print(f"  重复数: {len(duplicates)}")
    print(f"  语义漂移: {drift_count}")

    # 分类分布
    cats = {}
    for item in data:
        c = item.get("category", "unknown")
        cats[c] = cats.get(c, 0) + 1
    print(f"  分类: {cats}")

    # source 分布
    sources = {}
    for item in data:
        s = item["source"].split("_")[0]
        sources[s] = sources.get(s, 0) + 1
    print(f"  来源: {sources}")

    # 打印所有问题
    if issues:
        print()
        print("=" * 60)
        print(f"详细问题 ({len(issues)} 条):")
        for issue in issues:
            print(f"  {issue}")

    # 自动修复
    if auto_fix and (duplicates or any("id_gap" in s for s in issues)):
        print()
        print("执行自动修复...")
        # 去重 + 重新编号
        data = [item for item in data if item["id"] not in duplicates]
        for i, item in enumerate(data, 1):
            item["id"] = i
        # 覆盖写回
        output_path = dataset_path.replace(".json", "_fixed.json") if ".json" in dataset_path else dataset_path + "_fixed.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"修复后已保存: {output_path} ({len(data)} 条)")

    return len(issues)


def _extract_keywords(text: str) -> dict:
    """从问题中提取核心关键词。"""
    import re

    # 数字
    numbers = re.findall(r'\d+', text)

    # 实体词（中文专有名词 + 英文词）
    entities = []
    # 部门名
    for dept in ["销售", "技术", "运营", "人事", "财务", "市场"]:
        if dept in text:
            entities.append(dept)
    # 人名
    for name in ["张三", "李四", "王五", "郑十", "赵六"]:
        if name in text:
            entities.append(name)

    return {"numbers": numbers, "entities": entities}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="评估数据集质检")
    parser.add_argument("--dataset", type=str, default="eval_dataset_expanded.json",
                        help="数据集路径")
    parser.add_argument("--fix", action="store_true", help="自动修复可修复的问题")
    args = parser.parse_args()

    issues_count = check_quality(args.dataset, auto_fix=args.fix)
    if issues_count > 0:
        print(f"\n⚠️ 发现 {issues_count} 个问题，建议人工审查后决定是否修复")
    else:
        print("\n✅ 数据集通过质检")

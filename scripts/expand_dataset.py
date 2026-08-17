"""评估数据集自动扩充脚本。
对每条已有 case 用 LLM 生成 5 个同义变体，测试 Agent 对不同问法的鲁棒性。

用法:
    python scripts/expand_dataset.py                # 默认生成 5 个变体/条
    python scripts/expand_dataset.py --variants 8   # 生成 8 个变体/条
    python scripts/expand_dataset.py --dry-run      # 预览不保存
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.messages import HumanMessage

from app.config import settings

VARIANTS_PER_CASE = 5

VARIANT_PROMPT = """你是一个测试数据生成专家。请将以下用户问题改写为 {n} 个语义等价的变体。

## 原始问题
{question}

## 改写要求
1. 保持语义完全相同（查询意图、期望答案不变）
2. 变换句式、用词、语序、省略方式
3. 模拟真实用户的不同表达习惯：
   - 口语化："销售那边异常率多少啊"
   - 省略主语："11月异常率"
   - 换词："销售额" → "业绩" / "销售数据"
   - 疑问词变换："多少" / "是多少" / "怎么样" / "什么情况"
   - 时间表述变换："11月" / "11月份" / "上个月"
4. 每条变体一行，不要编号、不要引号包裹
5. 输出纯文本，每行一条"""


def expand_dataset(
    dataset_path: str = None,
    n_variants: int = VARIANTS_PER_CASE,
    dry_run: bool = False,
):
    """用 LLM 扩充评估数据集。"""
    if dataset_path is None:
        dataset_path = PROJECT_ROOT / "eval_dataset.json"

    with open(dataset_path, "r", encoding="utf-8") as f:
        original = json.load(f)

    print(f"原始数据集: {len(original)} 条")
    print(f"目标扩充: 每条生成 {n_variants} 个变体，预期 {len(original)} + {len(original) * n_variants} = {len(original) * (1 + n_variants)} 条")
    print()

    llm = ChatTongyi(
        model=settings.LLM_MODEL,
        dashscope_api_key=settings.DASHSCOPE_API_KEY,
        temperature=0.8,  # 高温度增加变体多样性
        max_tokens=500,
    )

    expanded = []
    next_id = 1

    for item in original:
        question = item["question"]
        category = item.get("category", "")
        print(f"[{item['id']}/{len(original)}] {question[:60]}...")

        # 保留原始条目
        original_item = dict(item)
        original_item["id"] = next_id
        original_item["source"] = f"original_{item['id']}"
        expanded.append(original_item)
        next_id += 1

        # 用 LLM 生成变体
        variants_generated = 0
        retries = 0

        while variants_generated < n_variants and retries < 3:
            prompt = VARIANT_PROMPT.format(question=question, n=n_variants)
            try:
                response = llm.invoke([HumanMessage(content=prompt)])
                raw = response.content.strip()

                # 解析变体：每行一条
                lines = [l.strip() for l in raw.split("\n") if l.strip()]
                # 去掉可能的编号前缀（"1. "、"1、"、"1）"等）
                import re
                cleaned = []
                for line in lines:
                    line = re.sub(r'^[\d]+[\.\、\)）\s]+', '', line).strip()
                    line = line.strip('"\'"').strip()
                    if line and line != question and len(line) >= 3:
                        cleaned.append(line)

                for variant_text in cleaned[:n_variants - variants_generated]:
                    expanded.append({
                        "id": next_id,
                        "question": variant_text,
                        "expected_intent": item["expected_intent"],
                        "expected_tools": item.get("expected_tools", []),
                        "expected_doc_keywords": item.get("expected_doc_keywords", []),
                        "reference_keywords": item.get("reference_keywords", []),
                        "category": category,
                        "source": f"variant_of_{item['id']}",
                    })
                    next_id += 1
                    variants_generated += 1

                if variants_generated < n_variants:
                    retries += 1
                else:
                    break

            except Exception as e:
                print(f"  ⚠ LLM 调用失败: {e}")
                retries += 1

        print(f"  → 生成 {variants_generated}/{n_variants} 个变体")

    print(f"\n总计: {len(expanded)} 条（原始 {len(original)} + 变体 {len(expanded) - len(original)}）")

    if dry_run:
        print("\n[预览模式] 不保存文件")
        for item in expanded[:10]:
            src = item.get("source", "")
            print(f"  [{src}] {item['question'][:80]}")
        return expanded

    # 保存
    output_path = PROJECT_ROOT / f"eval_dataset_expanded.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(expanded, f, ensure_ascii=False, indent=2)
    print(f"已保存: {output_path}")

    # 同时备份原始文件
    backup_path = PROJECT_ROOT / f"eval_dataset_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(original, f, ensure_ascii=False, indent=2)
    print(f"原始备份: {backup_path}")

    return expanded


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="扩充评估数据集")
    parser.add_argument("--variants", type=int, default=VARIANTS_PER_CASE,
                        help=f"每条生成的变体数（默认 {VARIANTS_PER_CASE}）")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式，不保存文件")
    args = parser.parse_args()

    expand_dataset(n_variants=args.variants, dry_run=args.dry_run)

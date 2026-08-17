"""Agent 评估体系 — 四维度评测脚本。

维度:
  1. 意图分类准确率     — classify_intent 是否匹配 expected_intent
  2. 工具选择准确率     — Agent 是否调用了 expected_tools（需 DB 有数据）
  3. RAG 检索命中率     — 3a: 关键词命中（基线） + 3b: LLM 语义相关性（主指标）
  4. 答案质量 LLM 评分  — LLM-as-Judge 对最终答案打 1-5 分

用法:
    python eval.py                        # 全量评估
    python eval.py --category data_query  # 只测某类
    python eval.py --skip-agent           # 只测意图分类 + RAG + LLM Judge（无需数据库）
    python eval.py --skip-rag-semantic    # 跳过语义 RAG，只保留关键词命中率（省钱）
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# —— 维度权重 ——
WEIGHTS = {
    "intent_accuracy": 0.25,
    "tool_selection": 0.25,
    "rag_hit_rate": 0.15,
    "answer_quality": 0.35,
}

# —— LLM-as-Judge Prompt ——
JUDGE_PROMPT = """你是一个评估专家。请对以下 AI 助手的回答质量打分。

## 用户问题
{question}

## 期望覆盖的关键词
{reference_keywords}

## AI 助手的回答
{answer}

## 评分标准（1-5 分）
- 5: 完全回答了问题，包含具体数据/条款，准确无误
- 4: 基本回答了问题，但缺少一些细节或深度
- 3: 部分回答了问题，有概括性信息但不够具体
- 2: 尝试回答但偏离主题，或信息明显不足
- 1: 未回答问题，或给出了错误信息

## 打分要求
请分别对以下三个维度打分，并给出简短理由：
1. relevance（相关性）：回答是否与问题相关
2. completeness（完整性）：是否覆盖了问题的各个方面
3. accuracy（准确性）：回答中的事实是否正确

输出 JSON 格式：
{{"relevance": 4, "completeness": 3, "accuracy": 4, "overall": 4, "reason": "简短理由"}}
只输出 JSON，不要其他内容。"""

# —— RAG 语义相关性 Judge Prompt ——
RAG_JUDGE_PROMPT = """你是一个检索质量评估专家。判断以下文档片段是否与用户问题**语义相关**。

## 用户问题
{question}

## 文档片段
{chunks}

## 判断标准
- relevant=true: 片段内容能帮助回答用户问题（直接相关或间接相关均可）
- relevant=false: 片段内容与问题无关，或只有噪音词汇重合

注意：
- 同义词替换算相关（如"超时工作"↔"加班"、"人员流动"↔"离职"）
- 只有表面词汇重合但语义无关的不算（如问"加班时长"出现"加班餐补标准"→不相关）
- 不确定时倾向于判断为相关（宁可多召回，不可漏判）

输出 JSON 数组（按片段顺序）：
[{{"index": 0, "relevant": true, "reason": "一句话理由"}}, ...]
只输出 JSON，不要其他内容。"""


def load_dataset(dataset_path: str = None) -> list[dict]:
    """加载评估数据集。"""
    if dataset_path is None:
        dataset_path = PROJECT_ROOT / "eval_dataset.json"
    with open(dataset_path, "r", encoding="utf-8") as f:
        return json.load(f)


# —————— 维度 1: 意图分类 ——————
def eval_intent(item: dict) -> dict:
    """评估意图分类准确率。"""
    from app.services.intent_router import classify_intent

    predicted = classify_intent(item["question"])
    expected = item.get("expected_intent", "")
    correct = predicted["intent"] == expected

    return {
        "question": item["question"],
        "expected": expected,
        "predicted": predicted["intent"],
        "confidence": predicted.get("confidence", 0),
        "correct": correct,
    }


# —————— 维度 2: 工具选择 ——————
def eval_tool_selection(item: dict, thread_id: str = "eval_default") -> dict | None:
    """评估 Agent 工具选择准确率。需要数据库中有数据。"""
    from app.services.query_agent import run_query

    expected_tools = set(item.get("expected_tools", []))
    if not expected_tools:
        return None  # 该问题不涉及工具选择

    try:
        result = run_query(item["question"], thread_id=thread_id)
        used_tools = set(result.get("tools_used", []))
    except Exception as e:
        return {
            "question": item["question"],
            "expected_tools": list(expected_tools),
            "used_tools": [],
            "precision": 0.0,
            "recall": 0.0,
            "error": str(e),
        }

    # 精确率：调用的工具中有多少是预期需要的
    precision = len(used_tools & expected_tools) / len(used_tools) if used_tools else 0.0
    # 召回率：预期工具中有多少被实际调用了
    recall = len(used_tools & expected_tools) / len(expected_tools) if expected_tools else 1.0

    return {
        "question": item["question"],
        "expected_tools": list(expected_tools),
        "used_tools": list(used_tools),
        "precision": round(precision, 2),
        "recall": round(recall, 2),
    }


# —————— 维度 3: RAG 检索命中率 ——————
def eval_rag_retrieval(item: dict) -> dict | None:
    """评估 RAG 检索是否命中了预期文档关键词。需要 ChromaDB 已索引。"""
    from app.services.rag.retriever import retrieve as hybrid_retrieve

    expected_keywords = item.get("expected_doc_keywords", [])
    if not expected_keywords:
        return None

    try:
        chunks = hybrid_retrieve(item["question"], top_k=5)
    except Exception as e:
        return {
            "question": item["question"],
            "expected_keywords": expected_keywords,
            "hit_count": 0,
            "total_chunks": 0,
            "hit_rate": 0.0,
            "error": str(e),
        }

    if not chunks:
        return {
            "question": item["question"],
            "expected_keywords": expected_keywords,
            "hit_count": 0,
            "total_chunks": 0,
            "hit_rate": 0.0,
        }

    # 统计有多少 chunks 包含至少一个预期关键词
    hit_count = 0
    for chunk in chunks:
        text = chunk.get("text", "")
        if any(kw in text for kw in expected_keywords):
            hit_count += 1

    return {
        "question": item["question"],
        "expected_keywords": expected_keywords,
        "hit_count": hit_count,
        "total_chunks": len(chunks),
        "hit_rate": round(hit_count / len(chunks), 2),
    }


# —————— 维度 3b: RAG 语义相关性（LLM-as-Judge） ——————
def eval_rag_semantic(item: dict) -> dict | None:
    """用 LLM 判断检索到的每个 chunk 是否与问题语义相关。

    相比纯关键词匹配，能正确处理同义替换、反向误判、跨 chunk 语义。
    """
    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_core.messages import HumanMessage

    from app.config import settings
    from app.services.rag.retriever import retrieve as hybrid_retrieve

    # 只测 doc_query 和 hybrid（需要 RAG 的场景）
    if item.get("expected_intent") not in ("doc_query", "hybrid"):
        return None

    try:
        chunks = hybrid_retrieve(item["question"], top_k=5)
    except Exception as e:
        return {
            "question": item["question"],
            "semantic_hit_rate": 0.0,
            "total_chunks": 0,
            "relevant_count": 0,
            "error": str(e),
        }

    if not chunks:
        return {
            "question": item["question"],
            "semantic_hit_rate": 0.0,
            "total_chunks": 0,
            "relevant_count": 0,
        }

    # 构建 chunks 文本（编号后喂给 LLM）
    chunks_text_parts = []
    for i, chunk in enumerate(chunks):
        text = chunk.get("text", "")[:300]  # 截断，节省 token
        title = chunk.get("doc_title", "")
        header = f"[{i}] 《{title}》" if title else f"[{i}]"
        chunks_text_parts.append(f"{header}\n{text}")
    chunks_text = "\n\n".join(chunks_text_parts)

    prompt = RAG_JUDGE_PROMPT.format(
        question=item["question"],
        chunks=chunks_text,
    )

    try:
        llm = ChatTongyi(
            model=settings.LLM_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.1,
            max_tokens=400,
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        judgments = json.loads(content)

        relevant_count = sum(1 for j in judgments if j.get("relevant"))
        semantic_hit_rate = round(relevant_count / len(chunks), 2)

        return {
            "question": item["question"],
            "semantic_hit_rate": semantic_hit_rate,
            "total_chunks": len(chunks),
            "relevant_count": relevant_count,
            "judgments": judgments,
        }
    except Exception as e:
        return {
            "question": item["question"],
            "semantic_hit_rate": 0.0,
            "total_chunks": len(chunks),
            "relevant_count": 0,
            "error": f"LLM 判断失败: {e}",
        }


# —————— 维度 4: LLM-as-Judge 答案质量 ——————
def eval_answer_quality(item: dict, answer: str) -> dict:
    """使用 LLM-as-Judge 对答案质量评分。"""
    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_core.messages import HumanMessage

    from app.config import settings

    if not answer or len(answer) < 10:
        return {
            "relevance": 1,
            "completeness": 1,
            "accuracy": 1,
            "overall": 1,
            "reason": "答案为空或过短",
        }

    prompt = JUDGE_PROMPT.format(
        question=item["question"],
        reference_keywords=json.dumps(item.get("reference_keywords", []), ensure_ascii=False),
        answer=answer,
    )

    try:
        llm = ChatTongyi(
            model=settings.LLM_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.1,
            max_tokens=300,
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(content)
        result.setdefault("overall", round(
            (result.get("relevance", 3) + result.get("completeness", 3) + result.get("accuracy", 3)) / 3, 1
        ))
        return result
    except Exception as e:
        return {
            "relevance": 0,
            "completeness": 0,
            "accuracy": 0,
            "overall": 0,
            "reason": f"评分失败: {e}",
        }


# —————— 获取答案（用于 LLM Judge） ——————
def get_answer(item: dict, thread_id: str = "eval_default") -> str:
    """根据意图类型获取答案。每条用独立 thread_id 避免测试间状态污染。"""
    from app.services.intent_router import classify_intent

    intent = classify_intent(item["question"])["intent"]

    try:
        if intent == "doc_query":
            from app.services.rag.service import ask_rag
            result = ask_rag(item["question"])
            return result.get("answer", "")
        elif intent == "hybrid":
            from app.services.query_agent import run_query
            result = run_query(item["question"], thread_id=thread_id)
            return result.get("answer", "")
        else:
            from app.services.query_agent import run_query
            result = run_query(item["question"], thread_id=thread_id)
            return result.get("answer", "")
    except Exception as e:
        return f"[获取答案失败: {e}]"


# —————— 主评估流程 ——————
def run_eval(
    dataset: list[dict],
    skip_agent: bool = False,
    skip_judge: bool = False,
    skip_rag_semantic: bool = False,
    category_filter: str = None,
):
    """执行全量评估并输出报告。"""
    items = dataset
    if category_filter:
        items = [it for it in items if it.get("category") == category_filter]
        print(f"筛选分类: {category_filter}，共 {len(items)} 条\n")

    results = {
        "meta": {
            "dataset_size": len(items),
            "eval_time": datetime.now().isoformat(),
            "skip_agent": skip_agent,
            "skip_judge": skip_judge,
            "skip_rag_semantic": skip_rag_semantic,
        },
        "intent": [],
        "tool_selection": [],
        "rag_retrieval": [],
        "rag_semantic": [],
        "answer_quality": [],
    }

    t0 = time.time()

    for i, item in enumerate(items, 1):
        q = item["question"]
        thread_tool = f"eval_{item['id']}_tool"   # 工具选择评估用独立 thread
        thread_judge = f"eval_{item['id']}_judge"  # LLM Judge 用独立 thread
        print(f"[{i}/{len(items)}] {q[:60]}...")

        try:
            # 维度 1: 意图分类（总是执行）
            intent_result = eval_intent(item)
            results["intent"].append(intent_result)
            icon = "✅" if intent_result["correct"] else "❌"
            print(f"  意图: {icon} expected={intent_result['expected']} predicted={intent_result['predicted']}")

            # 维度 2: 工具选择（需要 DB）
            if not skip_agent:
                tool_result = eval_tool_selection(item, thread_tool)
                if tool_result:
                    results["tool_selection"].append(tool_result)
                    if "error" in tool_result:
                        print(f"  工具: ⚠️ {tool_result['error'][:80]}")
                    else:
                        print(f"  工具: precision={tool_result['precision']} recall={tool_result['recall']}")

            # 维度 3a: RAG 关键词命中率（快速基线）
            rag_result = eval_rag_retrieval(item)
            if rag_result:
                results["rag_retrieval"].append(rag_result)
                if "error" in rag_result:
                    print(f"  RAG(kw): ⚠️ {rag_result['error'][:80]}")
                else:
                    print(f"  RAG(kw): hit_rate={rag_result['hit_rate']} ({rag_result['hit_count']}/{rag_result['total_chunks']})")

            # 维度 3b: RAG 语义相关性（LLM-as-Judge，主指标）
            if not skip_rag_semantic:
                rag_sem_result = eval_rag_semantic(item)
                if rag_sem_result:
                    results["rag_semantic"].append(rag_sem_result)
                    if "error" in rag_sem_result:
                        print(f"  RAG(sem): ⚠️ {rag_sem_result['error'][:80]}")
                    else:
                        print(f"  RAG(sem): semantic_hit_rate={rag_sem_result['semantic_hit_rate']} ({rag_sem_result['relevant_count']}/{rag_sem_result['total_chunks']})")

            # 维度 4: LLM-as-Judge
            if not skip_judge:
                answer = get_answer(item, thread_judge)
                quality = eval_answer_quality(item, answer)
                results["answer_quality"].append(quality)
                print(f"  质量: overall={quality['overall']} (r={quality['relevance']} c={quality['completeness']} a={quality['accuracy']})")

        except Exception as e:
            print(f"  ❌ 评估失败: {e}")
            results["intent"].append({
                "question": q, "expected": item.get("expected_intent", ""),
                "predicted": "ERROR", "correct": False, "error": str(e),
            })

        print()

    elapsed = round(time.time() - t0, 1)
    results["meta"]["elapsed_seconds"] = elapsed

    # —— 汇总报告 ——
    print("=" * 60)
    print("评估报告")
    print("=" * 60)

    # 意图分类
    intent_correct = sum(1 for r in results["intent"] if r["correct"])
    intent_acc = round(intent_correct / len(results["intent"]) * 100, 1) if results["intent"] else 0
    print(f"\n📌 意图分类准确率: {intent_acc}% ({intent_correct}/{len(results['intent'])})\t权重: {WEIGHTS['intent_accuracy']}")

    # 错误明细
    intent_errors = [r for r in results["intent"] if not r["correct"]]
    if intent_errors:
        for e in intent_errors:
            print(f"  ❌ \"{e['question'][:50]}\" → predicted={e['predicted']} (expected={e['expected']})")

    # 工具选择
    if results["tool_selection"]:
        avg_precision = round(sum(r["precision"] for r in results["tool_selection"]) / len(results["tool_selection"]), 2)
        avg_recall = round(sum(r["recall"] for r in results["tool_selection"]) / len(results["tool_selection"]), 2)
        print(f"\n📌 工具选择 — 平均精确率: {avg_precision}\t平均召回率: {avg_recall}\t权重: {WEIGHTS['tool_selection']}")
    else:
        avg_precision = 0
        avg_recall = 0
        print(f"\n📌 工具选择: 无测试用例（跳过）")

    # RAG 检索 — 关键词命中率（基线）
    if results["rag_retrieval"]:
        avg_hit_rate = round(sum(r["hit_rate"] for r in results["rag_retrieval"]) / len(results["rag_retrieval"]), 2)
        print(f"\n📌 RAG 关键词命中率（基线）: {avg_hit_rate}\t权重: {WEIGHTS['rag_hit_rate']}")
    else:
        avg_hit_rate = 0
        print(f"\n📌 RAG 关键词命中率: 无测试用例（跳过）")

    # RAG 检索 — 语义命中率（主指标）
    if results["rag_semantic"]:
        avg_sem_hit_rate = round(sum(r["semantic_hit_rate"] for r in results["rag_semantic"]) / len(results["rag_semantic"]), 2)
        print(f"📌 RAG 语义命中率（LLM-Judge）: {avg_sem_hit_rate}\t← 维度 3 主指标")
        # 打印语义 vs 关键词差异大的条目
        if results["rag_retrieval"] and len(results["rag_retrieval"]) == len(results["rag_semantic"]):
            diffs = []
            for kw, sem in zip(results["rag_retrieval"], results["rag_semantic"]):
                if kw["question"] == sem["question"]:
                    diff = sem["semantic_hit_rate"] - kw["hit_rate"]
                    if abs(diff) >= 0.4:
                        diffs.append((kw["question"][:50], kw["hit_rate"], sem["semantic_hit_rate"], diff))
            if diffs:
                print(f"\n  关键词 vs 语义差异 ≥0.4 的条目 ({len(diffs)} 条):")
                for q, kw_rate, sem_rate, diff in diffs:
                    direction = "↑语义更高" if diff > 0 else "↓关键词更高"
                    print(f"    {direction}: kw={kw_rate} sem={sem_rate} | {q}")
    else:
        avg_sem_hit_rate = 0
        print(f"📌 RAG 语义命中率: 无测试用例（跳过）")

    # 答案质量
    if results["answer_quality"]:
        avg_overall = round(sum(r["overall"] for r in results["answer_quality"]) / len(results["answer_quality"]), 1)
        avg_relevance = round(sum(r["relevance"] for r in results["answer_quality"]) / len(results["answer_quality"]), 1)
        avg_completeness = round(sum(r["completeness"] for r in results["answer_quality"]) / len(results["answer_quality"]), 1)
        avg_accuracy = round(sum(r["accuracy"] for r in results["answer_quality"]) / len(results["answer_quality"]), 1)
        print(f"\n📌 LLM-as-Judge 答案质量 (1-5):")
        print(f"   综合: {avg_overall}\t相关性: {avg_relevance}\t完整性: {avg_completeness}\t准确性: {avg_accuracy}")
        print(f"   权重: {WEIGHTS['answer_quality']}")
    else:
        avg_overall = 0

    # 综合评分（RAG 维度使用语义命中率）
    overall = (
        (intent_acc / 100) * WEIGHTS["intent_accuracy"]
        + avg_precision * WEIGHTS["tool_selection"]
        + avg_sem_hit_rate * WEIGHTS["rag_hit_rate"]
        + (avg_overall / 5) * WEIGHTS["answer_quality"]
    )
    overall_pct = round(overall * 100, 1)

    print(f"\n{'=' * 60}")
    print(f"🏆 综合评分: {overall_pct}%")
    print(f"⏱ 耗时: {elapsed}s")
    print(f"{'=' * 60}")

    # 保存详细结果
    report_path = PROJECT_ROOT / f"eval_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n详细报告已保存: {report_path}")

    return results


# —— CLI ——
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent 评估体系")
    parser.add_argument("--category", type=str, default=None,
                        help="只评估指定分类 (data_query / doc_query / hybrid / edge_case)")
    parser.add_argument("--skip-agent", action="store_true",
                        help="跳过工具选择评估（无需数据库）")
    parser.add_argument("--skip-judge", action="store_true",
                        help="跳过 LLM-as-Judge 评分")
    parser.add_argument("--skip-rag-semantic", action="store_true",
                        help="跳过 RAG 语义相关性评估（仅保留关键词命中率）")
    parser.add_argument("--dataset", type=str, default=None,
                        help="自定义数据集路径")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    run_eval(
        dataset,
        skip_agent=args.skip_agent,
        skip_judge=args.skip_judge,
        skip_rag_semantic=args.skip_rag_semantic,
        category_filter=args.category,
    )

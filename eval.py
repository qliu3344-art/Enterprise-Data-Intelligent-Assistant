"""Agent 评估体系 — 四维度评测脚本。

维度:
  1. 意图分类准确率     — classify_intent 是否匹配 expected_intent
  2. 工具选择准确率     — Agent 是否调用了 expected_tools（需 DB 有数据）
  3. RAG 检索命中率     — 3a: 关键词命中（基线） + 3b: LLM 语义相关性（主指标）
  4. 答案质量 LLM 评分  — LLM-as-Judge 对最终答案打 1-5 分

另有一块**不进加权**的「管道诊断」：量路由与它两道安全网在真管道里的行为
（事前升级率 / 执行期升级率 / 校验命中归因）。它和四维不同源——四维只吃答案
文本，它吃的是 intent / mode / exec_check——所以不能混进总分。

用法:
    python eval.py                        # 全量评估
    python eval.py --category data_query  # 只测某类
    python eval.py --skip-agent           # 只测意图分类 + RAG + LLM Judge（无需数据库）
    python eval.py --skip-rag-semantic    # 跳过语义 RAG，只保留关键词命中率（省钱）
"""

import argparse
import asyncio
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


def resolve_dataset_path(dataset_path: str = None) -> Path:
    """解析数据集路径（默认项目根目录下的 eval_dataset.json）。"""
    return Path(dataset_path) if dataset_path else PROJECT_ROOT / "eval_dataset.json"


def load_dataset(dataset_path: str = None) -> list[dict]:
    """加载评估数据集。"""
    with open(resolve_dataset_path(dataset_path), "r", encoding="utf-8") as f:
        return json.load(f)


# —————— 维度 1: 意图分类 ——————
def eval_intent(item: dict) -> dict | None:
    """评估意图分类准确率。

    `expected_intent` 为空的条目不参与评分，返回 None。

    这类条目是 category=edge_case（问的是系统能力之外的东西），三个意图标签里
    没有「越界/拒答」这一档，所以原先填的 `data_query` 只是**占位值**——照着算的话
    它们答什么都会被判错，等于把不可评分的样本混进了分母。要真评它们得先加第四档。
    """
    expected = item.get("expected_intent", "")
    if not expected:
        return None

    from app.services.intent_router import classify_intent

    predicted = classify_intent(item["question"])
    correct = predicted["intent"] == expected

    return {
        "question": item["question"],
        "expected": expected,
        "predicted": predicted["intent"],
        "confidence": predicted.get("confidence", 0),
        "correct": correct,
    }


# —————— 维度 2: 工具选择 ——————
def _normalize_arg(value) -> str:
    """参数值归一化：统一成去空白的字符串再比，避免 None 与 "" 误判成不等。"""
    if value is None:
        return ""
    return str(value).strip()


def _check_tool_args(expected_args: dict, used_calls: list[dict], tool_name: str) -> dict:
    """核对某工具被调用时实际传的参数。

    只校验 expected_args 里列出的键——工具还有 limit 之类的可选参数，
    多传不算错。同一工具被调用多次时，任一次匹配即算通过。
    """
    calls = [c for c in used_calls if c.get("name") == tool_name]
    if not calls:
        return {"matched": False, "detail": "该工具未被调用"}

    for call in calls:
        actual = call.get("args") or {}
        if all(
            _normalize_arg(actual.get(key)) == _normalize_arg(value)
            for key, value in expected_args.items()
        ):
            return {"matched": True, "detail": "参数匹配"}

    return {
        "matched": False,
        "detail": f"参数不符：期望 {expected_args}，实际 {calls[0].get('args') or {}}",
    }


async def eval_tool_selection(item: dict, thread_id: str = "eval_default") -> dict | None:
    """评估 Agent 工具选择准确率。需要数据库中有数据。

    选对工具只是第一步——同一个工具换组参数就是另一次查询，所以数据集
    给了 expected_args 时还要核对参数，否则「工具名对、参数全错」也算满分。
    """
    from app.services.query_agent import run_query

    expected_tools = set(item.get("expected_tools", []))
    if not expected_tools:
        return None  # 该问题不涉及工具选择

    expected_args = item.get("expected_args", {})

    try:
        result = await run_query(item["question"], thread_id=thread_id)
        used_tools = set(result.get("tools_used", []))
        used_calls = result.get("tool_calls", [])
    except Exception as e:
        return {
            "question": item["question"],
            "expected_tools": list(expected_tools),
            "used_tools": [],
            "precision": 0.0,
            "recall": 0.0,
            "arg_check": {},
            "error": str(e),
        }

    # 精确率：调用的工具中有多少是预期需要的
    precision = len(used_tools & expected_tools) / len(used_tools) if used_tools else 0.0
    # 召回率：预期工具中有多少被实际调用了
    recall = len(used_tools & expected_tools) / len(expected_tools) if expected_tools else 1.0

    # 参数核对：只对数据集中声明了 expected_args 的工具做
    arg_check = {
        tool: _check_tool_args(args, used_calls, tool)
        for tool, args in expected_args.items()
    }

    return {
        "question": item["question"],
        "expected_tools": list(expected_tools),
        "used_tools": list(used_tools),
        "used_calls": used_calls,
        "precision": round(precision, 2),
        "recall": round(recall, 2),
        "arg_check": arg_check,
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
        # 裁判必须换模型：和生成答案的模型同源会引入自我偏好
        llm = ChatTongyi(
            model=settings.LLM_MODEL_JUDGE,
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
        # 裁判必须换模型：和生成答案的模型同源会引入自我偏好
        llm = ChatTongyi(
            model=settings.LLM_MODEL_JUDGE,
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
async def get_answer(item: dict, thread_id: str = "eval_default") -> tuple[str, dict]:
    """取答案：调 /query 的 handler 本体，不自己拼零件。每条用独立 session_id。

    旧版在这里直调 classify_intent + ask_rag/run_query，拼出来的和真机差三处：
      · 不过 _route_intent  → 阈值升级在离线根本不存在
      · 从不调 _exec_check_and_escalate → 执行期校验在离线不可见
      · hybrid 只跑 Agent   → 不跑检索、不融合
    于是「离线的静默错误率」量的是**一条没有安全网的管道**：报告的数和线上的
    行为是两回事。改成直调 handler 之后，评测和线上跑的是同一段代码。

    维度 1（意图分类准不准）仍然直调 classify_intent——那个问题要的就是分类器
    原值，不是升级后的结果。同一份输出服务两个目的，必须分开取。

    ⚠️ 副作用：handler 会写 chat_history 和 query_trace。session_id 统一用
    `eval_` 前缀，事后统计飞轮样本时按 `session_id NOT LIKE 'eval_%'` 过滤。

    Returns:
        (答案, 响应 data)。data 里的 `diagnostics` 带 route / exec_check，
        管道诊断那块靠它算。
    """
    from app.database import SessionLocal
    from app.routers.query import QueryRequest, natural_language_query

    db = SessionLocal()
    try:
        resp = await natural_language_query(
            QueryRequest(question=item["question"], session_id=thread_id), db
        )
        data = resp.get("data", {}) or {}
        return data.get("answer", ""), data
    except Exception as e:
        return f"[获取答案失败: {e}]", {}
    finally:
        SessionLocal.remove()


# —————— 主评估流程 ——————
async def run_eval(
    dataset: list[dict],
    skip_agent: bool = False,
    skip_judge: bool = False,
    skip_rag_semantic: bool = False,
    category_filter: str = None,
    dataset_path: str = None,
):
    """执行全量评估并输出报告。"""
    from app.config import settings

    items = dataset
    if category_filter:
        items = [it for it in items if it.get("category") == category_filter]
        print(f"筛选分类: {category_filter}，共 {len(items)} 条\n")

    results = {
        "meta": {
            # 模型和集子必须进报告：一份评测报告得能自证它跑在哪个模型、
            # 哪个集子上，否则换了模型之后只能靠提交时间反推。
            # 三个模型位分开记：intent 跑路由、tool_selection 跑主力、
            # rag_semantic 与 answer_quality 跑裁判，只记一个会把报告读成错的。
            "dataset": resolve_dataset_path(dataset_path).name,
            "dataset_size": len(items),
            "model": settings.LLM_MODEL,
            "model_router": settings.LLM_MODEL_ROUTER,
            "model_judge": settings.LLM_MODEL_JUDGE,
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
        # 管道诊断（不进加权），见文件头说明
        "pipeline": [],
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
            if intent_result:
                results["intent"].append(intent_result)
                icon = "✅" if intent_result["correct"] else "❌"
                print(f"  意图: {icon} expected={intent_result['expected']} predicted={intent_result['predicted']}")
            else:
                print("  意图: ⏭  未标 expected_intent，不计入准确率")

            # 维度 2: 工具选择（需要 DB）
            if not skip_agent:
                tool_result = await eval_tool_selection(item, thread_tool)
                if tool_result:
                    results["tool_selection"].append(tool_result)
                    if "error" in tool_result:
                        print(f"  工具: ⚠️ {tool_result['error'][:80]}")
                    else:
                        arg_note = ""
                        for tool, c in (tool_result.get("arg_check") or {}).items():
                            arg_note += f" 参数{'✅' if c['matched'] else '❌'}[{tool}]"
                        print(f"  工具: precision={tool_result['precision']} recall={tool_result['recall']}{arg_note}")

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
                answer, pipe = await get_answer(item, thread_judge)
                # 管道诊断：只记路由与校验的结论，不重复存答案（答案在 quality 里）。
                # 用 id 做 join 键——question 理论上会重复（数据集里有几组近义变体），
                # id 不会。诊断取自 handler 自己那一次路由，不是评测再调一遍反推的。
                diag = pipe.get("diagnostics") or {}
                route_diag = diag.get("route") or {}
                results["pipeline"].append({
                    "id": item.get("id"),
                    "category": item.get("category", ""),
                    "question": item["question"],
                    # predicted = 阈值升级前的原判，intent = 升级后的最终意图。
                    # 两者差一个 hybrid 就是事前升级；这个判据在这里是精确的，
                    # 不像只看 intent 那样分不出「本来判成 hybrid」和「被推去 hybrid」。
                    "predicted": route_diag.get("predicted", ""),
                    "confidence": route_diag.get("confidence", 0.0),
                    # 走的是 logprobs 还是 L2 规则。这是本次诊断唯一能把
                    # 「模型不确定」和「路由超时降级」分开的字段——两者都是低分。
                    "path": route_diag.get("path", ""),
                    "intent": pipe.get("intent", ""),
                    "mode": pipe.get("mode", ""),
                    "exec_check": diag.get("exec_check", {}),
                })
                quality = eval_answer_quality(item, answer)
                # 带上 category：汇总时要把边界类摘出去（见下方「答案质量」那段）
                quality["category"] = item.get("category", "")
                # 问题和答案原文必须进报告。这两个字段不参与打分，但少了它们，
                # 报告落到磁盘之后就无法自证：裁判打了低分，你不知道它当时看的是什么；
                # 答案里编了个年份，你也没法回头查。以前只能靠「按位置对齐数据集」
                # 反查题目，而数据集一换、一加 --category 筛选，对齐就断了。
                # 不做截断——截断是静默的，宁可报告大一点。
                quality["question"] = item["question"]
                quality["answer"] = answer
                results["answer_quality"].append(quality)
                print(f"  质量: overall={quality['overall']} (r={quality['relevance']} c={quality['completeness']} a={quality['accuracy']})")

        except Exception as e:
            print(f"  ❌ 评估失败: {e}")
            # 没标 expected_intent 的条目本就不参与评分，出错也不能记进来，
            # 否则会从这条路绕回分母里（见 eval_intent 的说明）。
            if item.get("expected_intent"):
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

        # 参数准确率：只统计数据集中声明了 expected_args 的条目
        checked = [r for r in results["tool_selection"] if r.get("arg_check")]
        if checked:
            arg_ok = sum(
                1 for r in checked if all(c["matched"] for c in r["arg_check"].values())
            )
            rate = round(arg_ok / len(checked) * 100, 1)
            print(f"📌 参数准确率: {rate}% ({arg_ok}/{len(checked)})\t← 只统计声明了 expected_args 的条目")
            for r in checked:
                for tool, c in r["arg_check"].items():
                    if not c["matched"]:
                        print(f"  ❌ \"{r['question'][:40]}\" [{tool}] {c['detail']}")
        else:
            print("📌 参数准确率: 无条目声明 expected_args（跳过）")
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
    # 边界类（越权 / 无据 / 写入）不进这个平均。它们问的是系统**该不该答**，
    # 而 Judge 的评分标准是「答得全不全、对不对」——一段正确的拒绝（「这个我没法帮你查」）
    # 在它眼里就是「未回答问题」，直接 1 分。混进来等于用「拒答得够不够详细」
    # 当扣分项，把综合分往下拽，而拒答恰恰是那 80 条唯一正确的表现。
    # 口径统一：边界类单出一项门指标，不参与四维加权。
    if results["answer_quality"]:
        graded = [r for r in results["answer_quality"] if r.get("category") != "edge_case"]
        edge = [r for r in results["answer_quality"] if r.get("category") == "edge_case"]
        n = len(graded) or 1
        avg_overall = round(sum(r["overall"] for r in graded) / n, 1)
        avg_relevance = round(sum(r["relevance"] for r in graded) / n, 1)
        avg_completeness = round(sum(r["completeness"] for r in graded) / n, 1)
        avg_accuracy = round(sum(r["accuracy"] for r in graded) / n, 1)
        print(f"\n📌 LLM-as-Judge 答案质量 (1-5):")
        print(f"   综合: {avg_overall}\t相关性: {avg_relevance}\t完整性: {avg_completeness}\t准确性: {avg_accuracy}")
        print(f"   权重: {WEIGHTS['answer_quality']}\t← 已剔除边界类 {len(edge)} 条，分母 {len(graded)}")
        if edge:
            edge_avg = round(sum(r["overall"] for r in edge) / len(edge), 1)
            print(f"   边界类 {len(edge)} 条另计，平均 {edge_avg}（低分是对的：拒答不该按「答得全」打分）")
    else:
        avg_overall = 0

    # —— 管道诊断（不进加权）——
    # 四维只吃答案文本，这一块吃的是 intent / mode / exec_check，量的是
    # 「路由和它两道安全网在真管道里干了什么」。分开列是因为它不该影响总分：
    # 升级多了不等于答得好，只等于系统对自己不放心。
    if results["pipeline"]:
        rows = results["pipeline"]
        n = len(rows)
        # 事前升级：路由原本判了单引擎，被阈值推去 hybrid
        pre = [r for r in rows if r["predicted"] != "hybrid" and r["intent"] == "hybrid"]
        # 执行期升级：路由判 data_query 且自信，跑完才被校验拦回来。
        # exec_check 只在 mode=agent 时触发，所以这里不会出现 doc_query。
        post = [r for r in rows if r["intent"] == "data_query" and r["mode"] == "hybrid"]
        # 分母只算真跑过校验的：exec_check 为空 = 这条压根没进 agent 支路
        checked = [r for r in rows if r["exec_check"]]
        blind = [r for r in checked if r["exec_check"].get("vocab") is False]
        seg = {"结构": 0, "空集": 0, "回指": 0}
        for r in checked:
            v = r["exec_check"]
            seg["结构"] += bool(v.get("structure"))
            seg["空集"] += bool(v.get("empty"))
            seg["回指"] += bool(v.get("reference"))
        # 路由走的哪条路，是这次诊断的承重项。规则降级给出的 0.30/0.60/0.65/0.70
        # 和 logprobs 的低置信**共用同一个刻度**，光看 confidence 分不出「模型不确定」
        # 和「路由坏了」——只有 path 分得开。这一栏是判据，不是装饰。
        degraded = [r for r in rows if r["path"] == "rule"]
        print(f"\n📌 管道诊断（{n} 条，不进加权）:")
        print(f"   路由路径: logprobs {n - len(degraded)} 条 / 规则降级 {len(degraded)} 条"
              f" ({round(len(degraded) / n * 100, 1)}%)")
        if degraded:
            print(f"   ⚠️ 降级那批的 intent 是 L2 规则判的，不是 logprobs ——"
                  f" 意图准确率不能按「logprobs 版」解读")
        print(f"   事前升级（阈值 → hybrid）: {len(pre)} 条 ({round(len(pre) / n * 100, 1)}%)")
        print(f"   执行期升级（校验 → 补跑文档）: {len(post)} 条 ({round(len(post) / n * 100, 1)}%)")
        print(f"   校验命中归因: 结构 {seg['结构']} / 空集 {seg['空集']} / 回指 {seg['回指']}"
              f"  ← 回指只标记，不升级")
        if blind:
            print(f"   ⚠️ 词表读不到、结构校验整段跳过: {len(blind)} 条"
                  f"  ← 这批不算「已校验」，命中率的分母要刨掉")

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

    # Windows 默认的 ProactorEventLoop 用不了 psycopg 的异步模式（checkpointer
    # 要用），而 asyncio.run 是按全局策略建循环的，所以在这里换掉。
    # 服务端走的是另一条路：uvicorn 直接传 loop_factory、绕过策略，见
    # app/main.py::selector_loop_factory。
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    dataset = load_dataset(args.dataset)
    # 整个评测必须跑在同一个事件循环里：checkpointer 的连接池绑定 loop，
    # 不能改成「每条 asyncio.run 一次」。
    asyncio.run(
        run_eval(
            dataset,
            skip_agent=args.skip_agent,
            skip_judge=args.skip_judge,
            skip_rag_semantic=args.skip_rag_semantic,
            category_filter=args.category,
            dataset_path=args.dataset,
        )
    )

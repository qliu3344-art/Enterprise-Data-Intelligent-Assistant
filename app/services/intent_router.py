"""意图路由器：用 logprobs 读模型的真实分布，而不是让它自报置信度。

自评置信度是模型的第二次生成，是一次新的、可能被 prompt 引导的表述；logprobs 是
第一次生成时就已存在的内部状态，无法伪装。
一句话记住：概率是模型自己的，不是它嘴上说的。

四个上线坑：
  1. temperature 必须保持 1 —— 它在 softmax 之前作用，设 0.01 会把分布人为压陡，
     p_top 虚高到 0.99 以上，阈值判断彻底失真（成对实测结论：污染成立）
  2. top_logprobs 固定 5 —— 合法区间 [0,5]，越界直接 HTTP 400；写死常量就不会踩
  3. DashScope 返回结构与 OpenAI 不同 —— 用薄适配层归一化成 {token: logprob}
  4. 标签必须单 token —— 否则拿到的是首 token 处的边缘概率，而且它不报错
"""

import hashlib
import json
import math
import time

from dashscope import Generation

from app.config import settings
from app.logger import logger
from app.retry import (
    ROUTE_BASE_DELAY,
    ROUTE_MAX_ATTEMPTS,
    ROUTE_TIMEOUT_SECONDS,
    retry_call,
)

# —— 阈值：由成本推导，不拍脑袋 ——
# 误判代价不对称：高置信度走错路，用户直接拿到答非所问的答案，代价 C_e；
# 低置信度触发安全网，只是多跑一路 hybrid，代价 C_h。
# 只有「期望的错误代价」超过「安全网成本」时才值得走安全网：
#     (1 - p_top) × C_e > C_h   →   p_top < 1 - C_h / C_e
# 取 C_h = 1、C_e = 4，解出来就是 0.75。
# 对照 Microsoft 的升级阈值分档（低风险 0.60 / 中风险 0.75 / 高风险 0.88），
# 我们取的是中风险档。
INTENT_CONFIDENCE_THRESHOLD = 0.75

# —— 设计点 1：标签必须单 token ——
# 标签写成单个汉字，是因为 tokenizer.encode("data_query") 会被切成 2 个 token
# （data / query），而 top_logprobs 是按位置返回的：多 token 标签只能拿到首 token
# 处的边缘概率，无法沿 token 树求和还原成整段标签的联合概率。
# 更麻烦的是它不会报错，只会安静地给出一个偏高的错误概率——静默错误，最危险的失败模式。
# 所以「要么换写法，要么换 tokenizer」，这里换的是写法。
LABEL_TO_INTENT = {"数": "data_query", "文": "doc_query", "混": "hybrid"}
INTENT_TO_LABEL = {label: intent for label, intent in LABEL_TO_INTENT.items()}

# —— 设计点 2：max_tokens=1 + temperature 保持 1 ——
# temperature 保持 1，也就是模型的原始分布——这里和常规调用的直觉是反的：温度越低
# 越「稳定」，但被温度压过的分布已经不是你该据以判断的那个分布了。
# 输出长度交给 max_tokens=1 控制——这两件事本来就该分开。
ROUTE_TEMPERATURE = 1
ROUTE_MAX_TOKENS = 1

# 算 margin 和做 label_mass 诊断都需要看到完整的前几个候选，本来就没有动态传参的理由
TOP_LOGPROBS = 5

INTENT_PROMPT = """判断下面这句话属于哪一类，只回答一个字，不要任何解释、不要标点。

数：查询数据库中的结构化数据（统计、数量、业绩、指标值、排名、异常率、质量分、某个部门或某个人的数据）
文：查询公司制度、规定、政策、标准、流程（怎么算、怎么判定、定义、规则、条件）
混：需要同时查数据库和文档才能完整回答（问「为什么」且同时涉及具体数据和制度依据）

用户问题：{question}

只回答一个字（数／文／混）："""


# —— 设计点 1 的上线前校验（L0 参数防线）——
# 结果缓存：启动时验一遍，之后每次路由直接命中，不重复加载 tokenizer。
# 加载 tokenizer 可能有秒级开销（首次还会联网查一次 etag），绝不能落进请求路径。
_verified_labels: dict[str, int] | None = None


def verify_single_token_labels(force: bool = False) -> dict[str, int]:
    """逐个校验标签的 token 长度，返回 {标签: token 数}。

    为什么必须在服务启动时做：多 token 标签不会报错。它会返回一个看起来完全正常的
    数字，只是那个数字是错的。宁可服务起不来，也不能带着错的置信度上线。
    任一路径不满足就抛 RuntimeError，让启动直接失败。

    Args:
        force: 标签表热更新后传 True 重跑校验；此时若校验不过应当拒绝更新，
               而不是线上悄悄降级成一个错的概率。
    """
    global _verified_labels
    if _verified_labels is not None and not force:
        return _verified_labels

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(settings.INTENT_TOKENIZER)
    lengths = {label: len(tokenizer.encode(label)) for label in LABEL_TO_INTENT}

    bad = {label: n for label, n in lengths.items() if n != 1}
    if bad:
        raise RuntimeError(
            f"意图标签单 token 校验失败：{bad}。"
            f"多 token 标签只能拿到首 token 处的边缘概率，不会报错但概率是错的。"
            f"请换标签写法，或换 tokenizer（当前 {settings.INTENT_TOKENIZER}）。"
        )

    _verified_labels = lengths
    return lengths


def _ensure_labels_verified() -> None:
    """路由入口的兜底校验（启动已验过则直接命中缓存，零开销）。"""
    verify_single_token_labels()


# —— 坑 3：薄适配层 ——
def _to_token_map(raw_top) -> dict[str, float]:
    """把候选列表归一化成 {token: logprob}。

    OpenAI：top_logprobs 是 [{"token": "数", "logprob": -0.01}, ...]
    DashScope：token 直接当键，形如 [{"数": -0.01, "文": -5.2, ...}]，
               嵌套层级也与 OpenAI 不同，不能照抄 OpenAI 的解析代码。
    上层业务只认归一化后的结构——将来换厂商、或者做双通道兜底，业务代码都不用动。
    """
    if raw_top is None:
        return {}
    if isinstance(raw_top, dict):
        raw_top = [raw_top]

    out: dict[str, float] = {}
    for item in raw_top:
        if not hasattr(item, "items"):
            continue
        if "token" in item and "logprob" in item:
            out[str(item["token"])] = float(item["logprob"])
        else:
            for k, v in item.items():
                if isinstance(v, (int, float)):
                    out[str(k)] = float(v)
    return out


def _normalize_top_logprobs(choice) -> tuple[str, float, dict[str, float]]:
    """从原生返回里取出第一个位置的分布，归一化成 (选中 token, 选中 logprob, {token: logprob})。"""
    logprobs = choice.get("logprobs") or {}
    if isinstance(logprobs, (list, tuple)):
        entries = list(logprobs)
    else:
        entries = list(logprobs.get("content") or [])

    if not entries:
        # 返回体里根本没有 logprobs 字段 —— A 类失败，交给 L2
        raise ValueError("返回体里没有 logprobs 字段")

    first = entries[0]
    if "token" in first:
        chosen_token = str(first.get("token", ""))
        chosen_lp = float(first.get("logprob", 0.0))
        top = _to_token_map(first.get("top_logprobs"))
    else:
        # token 直接当键的形态：取概率最大的那个作为选中 token
        top = _to_token_map(first)
        if not top:
            raise ValueError("logprobs 结构无法解析")
        chosen_token, chosen_lp = max(top.items(), key=lambda kv: kv[1])

    if not top:
        raise ValueError("logprobs 候选列表为空")
    return chosen_token, chosen_lp, top


# —— 设计点 3：三个派生量 ——
def _derive(top: dict[str, float], chosen_token: str) -> dict | None:
    """从归一化分布算 p_top / label_mass / margin，并给出路由标签。

    关键一条：绝不重新归一化。把三个标签的概率除以它们的和，看着更像置信度，
    但正好抹掉了 label_mass 这个信号——质量漏到垃圾 token 恰恰是最该报警的情况。

    Returns:
        None 表示前 5 个候选里一个合法标签都没有（prompt 约束失效，系统性问题）。
    """
    label_probs = {
        label: math.exp(lp) for label, lp in top.items() if label in LABEL_TO_INTENT
    }
    if not label_probs:
        return None

    # p_top：模型选中这个标签的概率，是阈值判断的主指标
    best_label = max(label_probs, key=label_probs.get)
    p_top = label_probs[best_label]

    # label_mass：三个合法标签的概率之和，衡量约束健康度。
    # 它低说明质量漏到了非法 token 上，是 prompt 约束失效，属于系统性问题，
    # 和「模型在两个合法标签之间犹豫」是两码事。
    label_mass = sum(label_probs.values())

    # margin：top1 与 top2 的差，用于区分「单选很确定」和「两个标签贴得很近」
    ranked = sorted((math.exp(lp) for lp in top.values()), reverse=True)
    margin = ranked[0] - ranked[1] if len(ranked) > 1 else ranked[0]

    return {
        "intent": LABEL_TO_INTENT[best_label],
        "label": best_label,
        "p_top": p_top,
        "label_mass": label_mass,
        "margin": margin,
        "chosen_token": chosen_token,
    }


# —— L2 规则降级 ——
# 规则匹配的是用户的问题，不是模型的输出。把模型的原始返回传给规则匹配函数，
# 等于拿英文标签名去匹配模型自己吐出来的英文标签，是循环论证，对中文用户问题完全无效。
#
# 中文没有词边界（\b），不能照搬正则，所以用关键词表做子串命中 + 优先级判定。
DOC_KEYWORDS = (
    "规定", "制度", "政策", "标准", "流程", "怎么算", "怎么判定", "定义", "规则", "条件",
)
DATA_KEYWORDS = (
    "数据", "统计", "数量", "业绩", "指标", "排名", "多少条", "异常率", "质量分", "部门",
)

# 规则路径的 confidence 上界就是阈值，写成常量卡住。规则给不出校准过的概率——
# 给 0.9 它就直接通过路由了，等于用一个没校准的规则冒充高置信度模型，安全网形同虚设。
# 低于阈值等于强制走安全网：降级路径的本质是把球踢给安全网，不是自己下结论。
RULE_CONFIDENCE_CAP = 0.70
RULE_CONF_DOUBLE_HIT = 0.65
RULE_CONF_SINGLE_HIT = 0.60
RULE_CONF_NO_HIT = 0.30


def rule_classify(question: str) -> tuple[str, float]:
    """L2 规则降级：关键词表 + 优先级判定，返回 (intent, 刻意压低的 confidence)。"""
    hits_doc = [k for k in DOC_KEYWORDS if k in question]
    hits_data = [k for k in DATA_KEYWORDS if k in question]

    if hits_doc and hits_data:
        return "hybrid", RULE_CONF_DOUBLE_HIT
    if hits_doc:
        return "doc_query", RULE_CONF_SINGLE_HIT
    if hits_data:
        return "data_query", RULE_CONF_SINGLE_HIT
    # 都不命中默认 data_query：它是猜错代价最小的分支——一次 SQL 加一次生成，最差是
    # 答非所问。如果默认 doc_query，走 RAG 会返回一堆不相关的制度条款，体验更差。
    # 降级的默认值要选错了代价最小的那个，这是设计逻辑，不是随手写的。
    return "data_query", RULE_CONF_NO_HIT


def _empty_metrics() -> dict:
    return {"p_top": 0.0, "label_mass": 0.0, "margin": 0.0, "top5": {}}


def _log_route(
    question: str,
    intent: str,
    metrics: dict,
    latency_ms: float,
    path: str,
    degrade: str,
) -> None:
    """每次路由落一条结构化日志 —— 让失败可诊断。

    top5 原始分布必须记：这是离线重新调阈值的唯一依据，没有它阈值就是拍脑袋。
    """
    q_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    top5 = {k: round(v, 4) for k, v in metrics["top5"].items()}
    logger.info(
        "intent_route | q_hash=%s | intent=%s | p_top=%.4f | label_mass=%.4f | "
        "margin=%.4f | latency_ms=%.1f | path=%s | degrade=%s | top5=%s",
        q_hash,
        intent,
        metrics["p_top"],
        metrics["label_mass"],
        metrics["margin"],
        latency_ms,
        path,
        degrade or "-",
        json.dumps(top5, ensure_ascii=False),
    )


def _rule_result(question: str, degrade: str, t0: float) -> dict:
    """走 L2 规则降级，组装与主路径同构的返回。"""
    intent, confidence = rule_classify(question)
    metrics = _empty_metrics()
    _log_route(question, intent, metrics, (time.perf_counter() - t0) * 1000, "rule", degrade)
    return {
        "intent": intent,
        "confidence": confidence,
        "path": "rule",
        "degrade": degrade,
        **metrics,
    }


def classify_intent(question: str) -> dict:
    """分类用户问题的意图。

    Returns:
        {
          "intent": str,        # data_query / doc_query / hybrid
          "confidence": float,  # = p_top，模型真实概率（不是自评）
          "p_top": float, "label_mass": float, "margin": float,
          "top5": {token: logprob},
          "path": "logprobs" | "rule",   # 走了哪条路径
          "degrade": str,                # 失败原因分类，正常为空串
        }
    """
    # 校验放在最前面、且在 try 之外：标签被切成多 token 是「配置错了」，不是
    # 「这次调用失败了」。不能让它掉进下面的降级分支——静默降级成一个错的概率，
    # 正是 L0 要堵死的东西。启动时已经验过一遍，这里通常直接命中缓存。
    _ensure_labels_verified()

    # 计时从「真正的路由调用」开始，一次性配置校验不算进路由耗时
    t0 = time.perf_counter()

    try:
        choice = _call_llm_with_logprobs(question)
        chosen_token, _chosen_lp, top = _normalize_top_logprobs(choice)
    except Exception as e:
        # A 类失败：压根没拿到分布（网络超时、限流、参数报错、返回体里没有 logprobs 字段）
        logger.warning(f"意图路由未拿到分布，进入 L2 规则降级: {type(e).__name__}: {e}")
        return _rule_result(question, f"no_distribution:{type(e).__name__}", t0)

    derived = _derive(top, chosen_token)
    if derived is None:
        # 有分布，但前 5 个候选里一个合法标签都没有 —— prompt 约束失效，只能靠规则
        logger.warning(
            f"前 5 个候选里没有合法标签，进入 L2 规则降级: "
            f"{list(top.keys())}"
        )
        result = _rule_result(question, "no_legal_label", t0)
        result["top5"] = top
        return result

    metrics = {
        "p_top": derived["p_top"],
        "label_mass": derived["label_mass"],
        "margin": derived["margin"],
        "top5": top,
    }
    _log_route(question, derived["intent"], metrics, (time.perf_counter() - t0) * 1000,
               "logprobs", "")

    return {
        "intent": derived["intent"],
        "confidence": derived["p_top"],
        "path": "logprobs",
        "degrade": "",
        **metrics,
    }


def _call_llm_with_logprobs(question: str) -> dict:
    """调用 DashScope，取回第一个 token 的分布。

    只用生成位置 0：max_tokens=1 已经把输出压到最短，同时消掉「生成后面内容反过来
    影响首 token」的可能。
    """
    prompt = INTENT_PROMPT.format(question=question)
    resp = retry_call(
        Generation.call,
        model=settings.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        result_format="message",
        temperature=ROUTE_TEMPERATURE,
        max_tokens=ROUTE_MAX_TOKENS,
        logprobs=True,
        top_logprobs=TOP_LOGPROBS,
        timeout=ROUTE_TIMEOUT_SECONDS,
        max_retries=ROUTE_MAX_ATTEMPTS,
        base_delay=ROUTE_BASE_DELAY,
    )

    status = getattr(resp, "status_code", None)
    if status != 200:
        raise RuntimeError(f"DashScope 返回 {status}: {getattr(resp, 'message', '')}")

    choices = resp.output.choices
    if not choices:
        raise ValueError("DashScope 返回体里没有 choices")
    return choices[0]

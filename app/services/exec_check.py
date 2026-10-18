"""执行期校验：路由够不到的那部分。

意图路由是事前的，手里只有问题文本。模型「很自信地错」的那批样本在那一侧没有
任何信号——置信度 0.999995，要在路由侧拦掉它，阈值得提到 1.0，那等于取消路由。
而且事前信号（logprobs / 换一版 prompt / 拿第二个模型投票）全都取自同一份问题
文本，信息源没变，读多少遍都读不出新东西。

执行期不一样：走到这里，模型实际调了什么工具、拿回什么结果，都已经落袋。这两样
是可以被证伪的——「这个取值库里没有」是二值的，不像概率那样会骗人。

三段，按信号的确定性从硬到软：

  1. 结构校验  参数取值对一遍库里真实存在的取值。硬——把「猜测」换成「查表」。
  2. 空集语义  零行，而问题预设了答案非空。挂在前一段通过的前提上，两段互斥。
  3. 回指校验  结果能否解释原问题。只标记、不拦截——它判的是语义，本身会错，
               而一个会错的校验不该有拦截权。

前两段命中即「走错路」，由调用方补跑文档支路；第三段只进日志。
"""

import threading

from app.logger import logger

# 只列真正有「取值集合」的参数。employee_name 不在此列：人名是开放集，穷举不了，
# 也不该穷举——库里没有这个人不等于问题走错路，可能只是这批还没采到。limit 和
# 日期范围同理，它们是区间不是词表。
PARAM_LABELS = {"data_type": "数据类型", "department": "部门"}

_VOCAB: dict | None = None
_VOCAB_LOCK = threading.Lock()


def _read_vocabulary() -> dict:
    """读库自己的词表：真实出现过哪些 data_type、哪些部门。

    不问模型，问库——数据库不会很自信地声称自己有一个不存在的数据类型。
    部门的取值散落在标准列和 business_data 的多个键下，所以复用查询层那份
    DEPARTMENT_KEYS，免得两处词表口径各漂各的。
    """
    from sqlalchemy import distinct

    from app.database import SessionLocal
    from app.models.cleaned_record import CleanedRecord
    from app.services.data_query_ops import DEPARTMENT_KEYS

    db = SessionLocal()
    try:
        data_types = {
            v for (v,) in db.query(distinct(CleanedRecord.data_type)).all() if v
        }
        departments = {
            v for (v,) in db.query(distinct(CleanedRecord.department)).all() if v
        }
        for key in DEPARTMENT_KEYS:
            col = CleanedRecord.business_data[key].as_string()
            departments |= {v for (v,) in db.query(distinct(col)).all() if v}
        return {"data_type": data_types, "department": departments}
    finally:
        db.close()


def _vocabulary() -> dict | None:
    """词表只读一次，之后走内存。读不到就返回 None，调用方跳过这一整段校验。

    读不到（库没起 / 连接抖动）时绝不能拦：这个校验是增强，不是安全闸。拦下去
    等于给数据查询凭空新增一个全站故障模式，而它本来只是想少答错几条。
    """
    global _VOCAB
    if _VOCAB is not None:
        return _VOCAB
    with _VOCAB_LOCK:
        if _VOCAB is None:
            try:
                _VOCAB = _read_vocabulary()
                logger.info(
                    "执行期校验词表已加载：%d 个数据类型 / %d 个部门"
                    % (len(_VOCAB["data_type"]), len(_VOCAB["department"]))
                )
            except Exception as e:
                logger.warning(f"执行期校验词表读取失败，本次跳过结构校验: {e}")
                return None
    return _VOCAB


def check_structure(tool_calls: list | None) -> list[str]:
    """第一段：工具参数的取值对词表。返回违规说明，空列表 = 通过。"""
    vocab = _vocabulary()
    if not vocab:
        return []
    bad: list[str] = []
    for call in tool_calls or []:
        args = (call or {}).get("args") or {}
        for param, label in PARAM_LABELS.items():
            value = args.get(param)
            if value and value not in vocab[param]:
                bad.append(f"{param}={value!r} 不在系统已有的{label}里")
    return bad


# 工具在「查到了，但是零条」时返回的原文，三句分别来自
# query_cleaned_records / get_summary_stats / get_anomaly_details。
EMPTY_MARKERS = ("没有匹配的数据记录", "暂无数据可统计", "没有找到匹配的异常记录")

# 预设了「答案非空」的疑问词。带这些词却拿到零行，大概率不是「答案是零」，而是
# 走错了路——问「有哪些产品」的人不会预期答案是「一个都没有」。反过来，制度性
# 问题问「有多少条不符合规范的」，真答案完全可能就是零，所以光看零行是有歧义的，
# 必须再挂上这个疑问词。
NONEMPTY_CUES = (
    "哪些", "哪个", "哪种", "多少", "几条", "几个", "几人", "谁",
    "排名", "最高", "最低", "最大", "最小", "top",
)


def check_empty(question: str, answer: str) -> bool:
    """第二段：零行，而问题预设了答案非空。"""
    if not any(m in (answer or "") for m in EMPTY_MARKERS):
        return False
    return any(c in (question or "").lower() for c in NONEMPTY_CUES)


# 问「为什么 / 依据 / 标准」的问题要的是因果或规则，而表格数据只能给共现。
# 判据是答案里有没有出现制度侧的落点：有落点不算数（可能是答非所问），
# 一个都没有，基本可以确定这条数据路答不了它。
CAUSE_CUES = (
    "为什么", "原因", "依据", "根据什么", "怎么算", "凭什么", "规则", "口径", "判定标准",
)
POLICY_MARKERS = ("制度", "条款", "规定", "第", "依据", "因为", "根据", "办法", "流程")


def check_reference(question: str, answer: str) -> bool:
    """第三段：结果能否解释原问题。命中 = 存疑，只标记。"""
    if not any(c in (question or "") for c in CAUSE_CUES):
        return False
    return not any(m in (answer or "") for m in POLICY_MARKERS)


def inspect_execution(question: str, tool_calls: list | None, answer: str) -> dict:
    """三段跑一遍。blocked = 前两段有命中，调用方据此补跑文档支路。"""
    structure = check_structure(tool_calls)
    # 第二段挂在第一段上：第一段已命中说明这次查询本身就不成立，不必再判一次。
    # 两段因此互斥，同一条错误不会被记两次。
    empty = not structure and check_empty(question, answer)
    return {
        "structure": structure,
        "empty": empty,
        "reference": check_reference(question, answer),
        "blocked": bool(structure) or empty,
        # 词表读不到时第一段整段没跑，可 structure 同样是 []——和「查过、干净」
        # 长得一模一样。这一位是给飞轮用的：只有它能把「验过」和「没验」分开，
        # 否则读库抖动的那批会被当成干净的负样本吃进去。`_vocabulary()` 走内存缓存。
        "vocab": _vocabulary() is not None,
    }

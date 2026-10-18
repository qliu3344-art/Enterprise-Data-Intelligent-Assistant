"""业务口径记忆：业务规则 / 指标口径的向量化存储。

复用 RAG 已搭建的 BGE Embedding 与同一个 PostgreSQL 库（pgvector），用独立的
表（business_conventions），与制度条款（doc_chunks）隔离。

定位说明（为什么是「口径」而不是「用户画像」）：
  企业数据查询的数据是客观、公开、透明的，不存在「用户偏好」。真正值得
  跨会话沉淀的是「业务口径 / 规则」——用户/业务交代过的指标定义、字段
  含义、业务规则，说一次长期生效。

  口径全是「组织维度」的，没有一层是个人维度，所以全局共享不按用户隔离，
  也就没有「跨用户串记忆」这个隐患。但它是三层作用域，不是一个池子：

    公司级正式口径 —— 权威定义在制度文档里（`_doc_already_defines`），
                     本表只放**候选**，审核通过才算数。
    部门级口径     —— 只在点名了该部门的提问里召回。
    会话级临时限定 —— **不进本表**，留在滑动窗口和摘要里就够了。

  旧实现把三者当一个东西，用一个池子 + 一套 200 条上限管，结果是：会话级
  临时限定撑满池子、部门规则串到别的部门、一年用一次的重要口径被 LRU 淘汰。

核心原则：按需注入，不是全量注入 —— 只召回最相关的口径拼进上下文。

生命周期按作用域分片：
  写入侧 —— 会话级丢弃；公司级先查制度文档，已有定义就不新建；部门级要求
            能判出部门名，判不出就丢弃而不是冒名顶替。改口是**版本更替**
            （旧版本设终止日期保留），不是覆盖。
  读取侧 —— 候选只在它诞生的会话里可见（「用」和「信」分开），生效口径才全局可见；
            相似度低于 RECALL_MIN_SCORE 的直接丢弃且不刷新访问时间。
  淘汰   —— 正式口径不参与 LRU；部门口径超配额降级为候选；候选池超配额或停滞则删除。

⚠️ 相似度标度与 vector_store 用同一个 similarity_expr（= 0.5 + 0.5·cos），
下面三个阈值全都标定在这个标度上，换标度等于换阈值。
"""

from datetime import date, timedelta

from langchain_core.messages import SystemMessage
from sqlalchemy import and_, delete, func, insert, literal, or_, select, update

from app.database import SessionLocal
from app.logger import logger
from app.models.business_convention import (
    CANDIDATE_QUOTA,
    DEPT_QUOTA,
    STALE_DAYS,
    SCOPE_DEPARTMENT,
    SCOPE_GLOBAL,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    BusinessConvention,
)
from app.services.rag.embedder import embed_documents, embed_query
from app.services.rag.vector_store import ensure_schema, search, similarity_expr

# 去重/更新阈值：相似度超过此值视为「同一条口径」（重复说或改口）
DEDUP_THRESHOLD = 0.9
# 直接覆盖阈值：相似度超过此值直接判「更新」，无需 LLM 确认
CONFLICT_THRESHOLD = 0.95

# 召回相似度下限：低于此值视为「与当前问题无关」，既不返回也不计入访问。
# 不设此值的话，任何问题都会召回 top_k 条（矮子里拔将军）并给它们续命，
# 导致超期遗忘永远不触发、LRU 淘汰的也不是真正最久未用的口径。
#
# 0.75 是在语料上量出来的，不是拍的 —— 跑 scripts/measure_recall_threshold.py
# 复现：40 条真实口径 × 40 条相关查询 + 25 条离题查询，算各自 top1 的分布，
# 相关查询最低 0.7996，离题查询最高 0.7685，可行区间 (0.7685, 0.7996)。
# 取 0.75：往下留 0.05 余量，仍拦掉 22/25 的离题查询、零误杀相关查询。
#
# 「往下偏」是刻意的，两个方向的代价不对称：漏掉一条相关口径 = 模型按自己的理解
# 算，答错但读起来通顺；漏拦一条离题口径只是少做一次清理。前者是隐性故障。
#
# ⚠️ 这个值标定在 0.5+0.5·cos 上。写成裸余弦的直觉值（原来的 0.35）在这套标度下
# 等于 cos < -0.30，中文句向量根本到不了 —— 那个下限从来没有生效过。
RECALL_MIN_SCORE = 0.75

# 制度文档粗筛分：判断「这条口径制度文档里是不是已经有了」时，先用向量筛一遍
# 再调 LLM 定性。
#
# ⚠️ 这个数只能粗筛，**做不了判断** —— 实测两个分布是重叠的（8 份制度 / 51 个
# chunk 的语料上量的 top1）：
#   文档里确实有的口径 → 落在 0.7799 ~ 0.8481
#   文档里没有的口径   → 落在 0.6682 ~ 0.7902
# 分不开：「合同额指含税合同金额」文档里有，0.7799；「销售部考核按签约额算」
# 文档里没有，0.7902 —— 后者比前者还高。原因是拿一句话去比一个章节，余弦相似度
# 有一个高地板：任何业务句子都能在 8 份文档里找到某个 0.75 上下的章节。
# 所以这里取 0.72（低于「文档里有的」那一侧的最低值，留 0.06 余量），
# 只负责挡掉明显无关的句子，不负责下结论。判「有没有」得靠 LLM。
DOC_PREFILTER_SCORE = 0.72

# 口径信号词：命中才触发 LLM 提取，避免每轮都消耗一次 LLM 调用
CONVENTION_HINTS = (
    "指的是", "记住", "口径", "定义", "以后", "叫做", "称为",
    "我们内部", "约定", "规则",
)

# 口径提取 Prompt。
#
# 作用域分流在这里一次做完，而不是先按信号词判一遍再调一次 LLM：信号词只能
# 判「是不是在说规则」，判不出适用范围，而适用范围正是这次调用顺手就能拿到的
# 东西（对话上下文都在 prompt 里）。
EXTRACT_PROMPT = """请从下面这段用户与数据助手的对话中，找出用户交代的「业务口径 / 规则」，并判断它属于哪一类。

三类，每条输出一行，格式为 `作用域|口径内容`：

1. 会话级 —— 标成 `会话`：只对这一次提问有效的临时限定，
   比如「这次只算华东区」「先按上个月看」。
   这类也要输出（调用方会丢掉它），不要因为它不重要就漏判。
2. 部门级 —— 标成 `部门:部门名`：只对某个部门生效的约定，
   比如「我们部门的考核按签约额算」。
   部门名必须能从对话上下文里推出来（「我们部门」指上文提到的那个部门）；
   推不出来就改判成公司级，**不要输出空的部门名**。
3. 公司级 —— 标成 `通用`：全公司通用的指标定义、字段含义、业务规则，
   比如「合同额指含税合同金额」。

要求：
1. 没有交代任何口径，只输出「无」
2. 每条一行，不要编号、不要解释、不要 markdown 代码块
3. 只输出口径本身，简洁准确

示例输出：
通用|合同额指含税合同金额
部门:销售部|销售部考核按签约额算，不按回款
会话|本次只统计华东区

用户问题：
{question}

助手回答：
{answer}

输出："""


def _has_convention_hint(text: str) -> bool:
    """判断文本是否含有「交代口径」的信号词。"""
    if not text:
        return False
    return any(hint in text for hint in CONVENTION_HINTS)


def _norm(text: str) -> str:
    """文本归一化，只用来判「是不是同一句话」，不做语义判断。"""
    return "".join((text or "").split()).rstrip("。.！!？?")


# ── 召回可见性 ──────────────────────────────────────────────────────
def _in_force_filter():
    """有效期过滤：只取当前正在生效的那个版本。

    NULL 表示那一侧不设边界（from 为空 = 一直有效，until 为空 = 仍是当前版本）。
    写成裸的 `effective_from <= today` 会把没填日期的口径静默排除掉。

    ⚠️ 已知局限：这里比的是**今天**，不是提问所指的那个时点。历史问题
    （「去年 11 月的异常率」）应当用去年 11 月的口径算，要做到那样得把问题里的
    时间点解析出来传进来。目前版本链是完整落库的，历史口径可恢复，但召回
    仍按今天选版本 —— 这是这一步没做完的部分，不是设计如此。
    """
    today = func.current_date()
    return and_(
        or_(
            BusinessConvention.effective_from.is_(None),
            BusinessConvention.effective_from <= today,
        ),
        or_(
            BusinessConvention.effective_until.is_(None),
            BusinessConvention.effective_until >= today,
        ),
    )


def _visible_filter(query: str, session_id: str | None):
    """按作用域 + 状态决定一条口径该不该被这次提问看到。

    三条可见路径，对应三类的权威性差异：

    1. 已生效的公司级口径 —— 所有会话可见。
    2. 已生效的部门级口径 —— 只有提问里点到了那个部门才可见。这一条就是
       「部门规则被别的部门看到」的修法：不依赖「提问者属于哪个部门」这种
       本项目没有的身份信息，靠的是提问本身有没有把部门说出来。
    3. 候选口径 —— 只对它诞生的那个会话可见。这就是「用」和「信」分开：
       说的人当次按自己说的算，推广给其他人要等审核。

    拿不到会话标识时不给候选放行：候选的语义就是「只对本会话生效」，
    没有会话标识就没有这个作用域。
    """
    clauses = [
        and_(
            BusinessConvention.status == STATUS_ACTIVE,
            BusinessConvention.scope == SCOPE_GLOBAL,
        ),
        and_(
            BusinessConvention.status == STATUS_ACTIVE,
            BusinessConvention.scope == SCOPE_DEPARTMENT,
            # 部门名出现在提问里才算命中。strpos 对 NULL 返回 NULL，天然排除
            # 没填部门的行。
            func.strpos(literal(query), BusinessConvention.department) > 0,
        ),
    ]
    if session_id:
        clauses.append(
            and_(
                BusinessConvention.status == STATUS_CANDIDATE,
                BusinessConvention.session_id == session_id,
            )
        )
    return or_(*clauses)


# ── 淘汰：按作用域分片 ──────────────────────────────────────────────
def _demote_over_quota(db) -> None:
    """部门口径超出部门配额时，把最久未访问的**降级为候选**，不直接删。

    降级是可恢复的：部门规则往往是本部门的人在用的，误淘汰比多留一条代价大得多。
    正式口径（公司级 + 已生效）不在这条路径上 —— 它不参与 LRU。
    """
    ranked = (
        select(
            BusinessConvention.id,
            func.row_number()
            .over(
                partition_by=BusinessConvention.department,
                order_by=BusinessConvention.last_accessed.desc(),
            )
            .label("rn"),
        )
        .where(
            BusinessConvention.scope == SCOPE_DEPARTMENT,
            BusinessConvention.status == STATUS_ACTIVE,
        )
        .subquery()
    )
    result = db.execute(
        update(BusinessConvention)
        .where(BusinessConvention.id.in_(select(ranked.c.id).where(ranked.c.rn > DEPT_QUOTA)))
        .values(status=STATUS_CANDIDATE)
    )
    if result.rowcount:
        logger.info(f"部门口径超配额：{result.rowcount} 条降级为候选（每部门 {DEPT_QUOTA} 条）")


def _purge_candidates(db) -> None:
    """候选池：先清停滞的，再按配额裁最久未访问的。

    候选池是唯一像样的「条数上限」该待的地方 —— 它是一个待办队列，队列需要
    有界。上限管在正式口径上就会变成「一年用一次的年终奖口径必然被挤掉」。
    """
    stale = db.execute(
        delete(BusinessConvention).where(
            BusinessConvention.status == STATUS_CANDIDATE,
            BusinessConvention.last_accessed
            < func.now() - func.make_interval(0, 0, 0, STALE_DAYS),
        )
    )
    if stale.rowcount:
        logger.info(f"候选口径停滞清理：删除 {stale.rowcount} 条（>{STALE_DAYS} 天未访问）")

    keep = (
        select(BusinessConvention.id)
        .where(BusinessConvention.status == STATUS_CANDIDATE)
        .order_by(BusinessConvention.last_accessed.desc())
        .offset(CANDIDATE_QUOTA)
        .subquery()
    )
    over = db.execute(
        delete(BusinessConvention).where(BusinessConvention.id.in_(select(keep.c.id)))
    )
    if over.rowcount:
        logger.info(f"候选池超配额：删除 {over.rowcount} 条最久未访问（上限 {CANDIDATE_QUOTA} 条）")


# ── 制度文档查重 ────────────────────────────────────────────────────
def _doc_already_defines(content: str, embedding: list[float]) -> bool:
    """公司级口径先查制度文档：已经有权威定义就不新建。

    口径唯一权威定义、全公司引用同一份、不允许各自解释 —— 这是指标口径治理的
    核心原则。靠用户随口说、LLM 提取、再存成一条全局记忆是重复建设，而且引入
    不一致：文档写的是 3 小时，某个用户记成 2 小时说了出来，全公司就开始按
    2 小时回答。

    只挡「同义复述」：文档已经写了同样的规则，用户只是又说了一遍。文档里有相关
    但**内容不一致**的说法（文档含税、用户说不含税）不在这里挡 —— 那是一条和
    权威定义冲突的说法，值得留成候选让人看一眼，而候选态本来就不对外生效。

    两步走：向量粗筛 + LLM 定性。向量不能单独用，理由见 DOC_PREFILTER_SCORE。
    """
    hits = search(embedding, top_k=3)
    if not hits or hits[0]["score"] < DOC_PREFILTER_SCORE:
        return False

    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_core.messages import HumanMessage

    from app.config import settings

    docs = "\n\n".join(
        f"《{h['metadata']['doc_title']}》\n{h['text'][:400]}" for h in hits
    )
    prompt = (
        "下面是公司制度文档里的几段原文，以及一条用户刚说出来的「业务口径」。\n"
        "请判断：制度文档里是不是**已经写明了同样这条口径**（用户只是把文档已有的"
        "内容又复述了一遍）？\n"
        "注意区分两种情况：\n"
        "- 文档里有同样规则 → 输出「已有」\n"
        "- 文档里没有，或者文档说的和它不一致（哪怕只是数值、口径范围不同）"
        " → 输出「没有」\n"
        "只输出「已有」或「没有」两个词之一。\n\n"
        f"用户说的口径：{content}\n\n"
        f"制度文档原文：\n{docs}\n\n"
        "输出："
    )
    try:
        llm = ChatTongyi(
            model=settings.LLM_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.0,
            max_tokens=20,
            model_kwargs={"request_timeout": settings.LLM_REQUEST_TIMEOUT_SECONDS},
        )
        resp = llm.invoke([HumanMessage(content=prompt)])
        raw = (resp.content or "").strip()
        if "没有" in raw:
            return False
        return "已有" in raw
    except Exception as e:
        # 判不了就当「文档里没有」：候选态本身无害（不审核不推广），
        # 多留一条给人看，比悄悄丢掉一条真口径划算。
        logger.warning(f"制度文档查重判定失败，按「未定义」处理: {e}")
        return False


def _confirm_update(old_text: str, new_text: str) -> bool:
    """灰色地带（相似度 0.9~0.95）判定：新口径是「同一条的改口更新」还是「两条不同规则」。

    向量相似度只看「像不像」，分不清「是不是反着改」，故此处调 LLM 定性。

    判定取向：只有明确得到「更新」才覆盖，语义不明一律保守判「不同」保留两条。
    改口会让旧版本下线，是有影响的动作；共存至少人能看出冲突。

    例外：LLM 调用失败时按「更新」处理 —— 此时拿不到任何信息，宁可更替版本，
    避免新旧口径同时被召回造成矛盾。
    """
    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_core.messages import HumanMessage

    from app.config import settings

    prompt = (
        "下面两条「业务口径」文本语义高度相似。请判断：第二条是「对第一条的改口/更新」"
        "（同一指标，用户修正了说法），还是「两条不同但相似的口径」（两个不同规则）？\n"
        "只输出「更新」或「不同」两个词之一。\n\n"
        f"第一条：{old_text}\n"
        f"第二条：{new_text}\n\n"
        "输出："
    )
    try:
        llm = ChatTongyi(
            model=settings.LLM_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.0,
            max_tokens=20,
            model_kwargs={"request_timeout": settings.LLM_REQUEST_TIMEOUT_SECONDS},
        )
        resp = llm.invoke([HumanMessage(content=prompt)])
        raw = (resp.content or "").strip()

        # 先判「不同」：模型可能输出「不相同」等变体 —— 含「相同」但不含「不同」，
        # 语义是「不同」，若用 "不同" not in raw 会被反向误判为更新
        if "不同" in raw:
            return False
        if "更新" in raw:
            return True

        # 两个关键词都没命中：语义不明，保守判「不同」保留两条，不更替版本
        logger.warning(f"口径冲突判定语义不明（{raw[:30]}），保守按「不同」保留两条")
        return False
    except Exception as e:
        logger.warning(f"口径冲突确认失败，默认按更新处理: {e}")
        return True


def save_convention(
    content: str,
    scope: str = SCOPE_GLOBAL,
    department: str | None = None,
    session_id: str | None = None,
    effective_from: date | None = None,
    metadata: dict | None = None,
) -> bool:
    """保存一条业务口径（作用域分流 → 去重/改口 → 分池淘汰）。

    Args:
        content: 口径文本（如「合同额指含税合同金额」）
        scope: SCOPE_GLOBAL / SCOPE_DEPARTMENT。传 'session' 直接丢弃 ——
               会话级临时限定不进长期记忆。
        department: scope=department 时必填；判不出部门名宁可丢弃，也不冒名顶替
        session_id: 来源会话，候选口径「本会话立即生效」的依据
        effective_from: 本条生效起始日，默认今天
        metadata: 额外元数据

    Returns:
        是否保存成功。完全重复的口径会刷新访问时间并返回 True；
        判定为改口时旧版本设终止日期、新版本顶上来。
    """
    content = (content or "").strip()
    if not content:
        return False

    if scope not in (SCOPE_GLOBAL, SCOPE_DEPARTMENT):
        # 会话级：它本来就是当轮对话的上下文，滑动窗口和摘要已经装着了。
        logger.debug(f"会话级口径不进长期记忆: {content[:40]}")
        return False

    department = (department or "").strip() or None
    if scope == SCOPE_DEPARTMENT and department is None:
        # 判不出部门的部门口径不能退化成全局口径 —— 那正是「部门规则被别的
        # 部门看到」的成因。宁可丢掉，也不 misattribute 到全公司。
        logger.warning(f"部门口径未能判出部门名，丢弃: {content[:40]}")
        return False

    ensure_schema()
    db = SessionLocal()
    try:
        embedding = embed_documents([content])[0]

        if scope == SCOPE_GLOBAL and _doc_already_defines(content, embedding):
            logger.info(f"制度文档已有该口径的权威定义，不新建: {content[:40]}")
            return True

        # 去重/改口只在同一个池子里比：跨池比较会把「加班超3小时算异常」和
        # 「销售部加班超3小时算异常」判成同一条。加 is_not_distinct_from 是为了
        # 让 department 为 NULL 的那一池也能用等值匹配。
        distance = BusinessConvention.embedding.cosine_distance(embedding)
        row = db.execute(
            select(
                BusinessConvention.id,
                BusinessConvention.content,
                BusinessConvention.scope,
                BusinessConvention.department,
                BusinessConvention.status,
                similarity_expr(distance).label("score"),
            )
            .where(
                BusinessConvention.scope == scope,
                BusinessConvention.department.is_not_distinct_from(department),
                _in_force_filter(),
            )
            .order_by(distance)
            .limit(1)
        ).first()

        new_from = effective_from or date.today()
        inherited = None

        if row is not None:
            same_text = _norm(row.content) == _norm(content)
            similarity = float(row.score)

            if same_text:
                # 同一句话被重复交代：刷新访问时间就够，不新建版本 ——
                # 否则同一句话每说一次就在库里多留一个内容相同的版本。
                now = func.now()
                db.execute(
                    update(BusinessConvention)
                    .where(BusinessConvention.id == row.id)
                    .values(updated_at=now, last_accessed=now)
                )
                db.commit()
                return True

            if similarity >= DEDUP_THRESHOLD and (
                similarity >= CONFLICT_THRESHOLD or _confirm_update(row.content, content)
            ):
                # 改口 = 版本更替，不是覆盖。旧版本设终止日期保留下来：历史问题
                # （去年 11 月的异常率）要用当时的口径算，覆盖掉就永久还原不出来。
                db.execute(
                    update(BusinessConvention)
                    .where(BusinessConvention.id == row.id)
                    .values(effective_until=new_from - timedelta(days=1))
                )
                # 新版本继承旧版本的 scope / department / status：改口是对一条
                # 已有定论的口径做修订，权威等级不变，不该换池子。
                inherited = (row.scope, row.department, row.status)
                logger.info(
                    f"口径改口（相似度 {similarity:.2f}）：{row.content[:30]} → {content[:30]}"
                )

        final_scope = inherited[0] if inherited else scope
        final_dept = inherited[1] if inherited else department
        final_status = inherited[2] if inherited else STATUS_CANDIDATE

        db.execute(
            insert(BusinessConvention).values(
                content=content,
                embedding=embedding,
                scope=final_scope,
                department=final_dept,
                status=final_status,
                # 只有候选态用得上来源会话；已生效的口径对所有会话可见
                session_id=session_id if final_status == STATUS_CANDIDATE else None,
                effective_from=new_from,
                meta=dict(metadata or {}),
            )
        )

        _demote_over_quota(db)
        _purge_candidates(db)

        db.commit()
        logger.info(
            f"业务口径已保存[{final_scope}/{final_status}]: {content[:50]}..."
        )
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"保存业务口径失败: {e}")
        return False
    finally:
        SessionLocal.remove()


def recall_conventions(
    query: str, session_id: str | None = None, top_k: int = 3
) -> list[dict]:
    """按语义召回与当前问题最相关的业务口径。

    Args:
        query: 当前问题
        session_id: 当前会话标识，决定本会话的候选口径能不能被看到
        top_k: 最多返回条数

    Returns:
        [{"text": str, "metadata": dict, "scope": str, "department": str, "score": float}, ...]
    """
    ensure_schema()
    db = SessionLocal()
    try:
        if not db.scalar(select(func.count()).select_from(BusinessConvention)):
            return []

        embedding = embed_query(query)
        distance = BusinessConvention.embedding.cosine_distance(embedding)
        rows = db.execute(
            select(
                BusinessConvention.id,
                BusinessConvention.content,
                BusinessConvention.meta,
                BusinessConvention.scope,
                BusinessConvention.department,
                similarity_expr(distance).label("score"),
            )
            .where(
                _visible_filter(query, session_id),
                _in_force_filter(),
                # 相似度下限下推到 SQL：留在 Python 侧的话 LIMIT 已经先把候选
                # 砍到 top_k 条了，排在第 4 名但过线的口径永远补不上来。
                similarity_expr(distance) >= RECALL_MIN_SCORE,
            )
            # 按裸距离排序，不要按 score 降序：包一层算术会让表达式不可索引。
            .order_by(distance)
            .limit(top_k)
        ).all()

        hits = [
            {
                "text": r.content,
                "metadata": dict(r.meta or {}),
                "scope": r.scope,
                "department": r.department or "",
                "score": round(float(r.score), 4),
            }
            for r in rows
        ]

        # 刷新命中口径的 last_accessed（部门口径与候选池的 LRU 依据）。
        # 未达阈值的不刷新 —— 否则无关问题也会给口径续命，遗忘机制形同虚设。
        hit_ids = [r.id for r in rows]
        if hit_ids:
            db.execute(
                update(BusinessConvention)
                .where(BusinessConvention.id.in_(hit_ids))
                .values(last_accessed=func.now())
            )

        db.commit()
        return hits
    except Exception as e:
        db.rollback()
        logger.error(f"召回业务口径失败: {e}")
        return []
    finally:
        SessionLocal.remove()


def build_convention_message(
    question: str, session_id: str | None = None, top_k: int = 3
) -> SystemMessage | None:
    """召回相关业务口径，组装成 SystemMessage（无相关口径时返回 None）。"""
    conventions = recall_conventions(question, session_id=session_id, top_k=top_k)
    if not conventions:
        return None

    # 部门口径带上归属：同一个问题可能同时命中公司级和部门级两条，
    # 不给出来源模型没法判断哪条优先。
    lines = [
        f"- {c['text']}" + (f"（{c['department']}口径）" if c["department"] else "")
        for c in conventions
    ]
    content = (
        "以下是已确认的业务口径/规则，回答与计算时必须遵循（不要主动提及口径本身）：\n"
        + "\n".join(lines)
    )
    return SystemMessage(content=content)


def extract_conventions(question: str, answer: str) -> list[dict]:
    """从一轮问答中提取新交代的业务口径，并判出作用域。

    先用信号词预筛，命中才调 LLM 提取，避免每轮都消耗一次 LLM 调用。

    Returns:
        [{"content": str, "scope": "global"|"department", "department": str|None}, ...]
        会话级临时限定**不在返回值里** —— 它本来就不该进长期记忆。
    """
    text = f"{question}\n{answer}"
    if not _has_convention_hint(text):
        return []

    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_core.messages import HumanMessage

    from app.config import settings

    prompt = EXTRACT_PROMPT.format(question=question[:500], answer=answer[:500])
    try:
        llm = ChatTongyi(
            model=settings.LLM_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.0,
            max_tokens=300,
            model_kwargs={"request_timeout": settings.LLM_REQUEST_TIMEOUT_SECONDS},
        )
        resp = llm.invoke([HumanMessage(content=prompt)])
        raw = (resp.content or "").strip()
        if not raw or raw in ("无", "空", "none", "None", "[]"):
            return []
        return _parse_extracted(raw)
    except Exception as e:
        logger.warning(f"业务口径提取失败: {e}")
        return []


def _parse_extracted(raw: str) -> list[dict]:
    """解析提取结果：每行 `作用域|口径内容`。

    模型不按格式走时不报错、也不丢内容 —— 整行当公司级收下。
    「无格式」比「格式错」常见得多，为一行的格式问题丢掉整轮提取不划算。
    """
    out: list[dict] = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-*# ").strip()
        if not line or line in ("无", "空"):
            continue

        if "|" not in line:
            out.append({"content": line, "scope": SCOPE_GLOBAL, "department": None})
            continue

        head, _, body = line.partition("|")
        head, body = head.strip(), body.strip()
        if not body:
            continue

        if head.startswith("会话") or head.startswith("本次"):
            continue  # 会话级不进长期记忆

        if head.startswith("部门"):
            dept = head.split(":", 1)[1].strip() if ":" in head else ""
            if dept:
                out.append(
                    {"content": body, "scope": SCOPE_DEPARTMENT, "department": dept}
                )
                continue
            # 部门名推不出来 → 按公司级候选收下，不冒名顶替某个部门

        out.append({"content": body, "scope": SCOPE_GLOBAL, "department": None})
    return out

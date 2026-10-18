"""业务口径长期记忆（向量表）。

复用 RAG 同一套 BGE Embedding 与同一个 PostgreSQL 库，但**独立成表**，
不跟制度条款挤在一张表里加判别列。理由是唯一会静默出错的那个查询：
检索侧建 BM25 时要全量导出语料——一旦漏了「只要制度条款」这个条件，
口径会混进 BM25 索引，被当成制度条款检索出来，还带着《》格式的引用。
分表让这个错误不可表达。

定位说明（为什么是「口径」而不是「用户画像」）：
  企业数据查询的数据是客观、公开、透明的，不存在「用户偏好」。真正值得
  跨会话沉淀的是「业务口径 / 规则」——用户或业务交代过的指标定义、字段
  含义、业务规则，说一次长期生效。例如「合同额指含税合同金额」「异常率
  = 异常记录数 / 总记录数」。

作用域（`scope`）是这张表的一等维度，不是 metadata 里的自由字段：
  三类的权威来源、生效范围、变更频率完全不同，共享一套生命周期必然出问题。
  - `global`（公司级）：全公司生效。**候选态待审核**，审核通过才算数。
  - `department`（部门级）：只在该部门的语境下生效。
  - 会话级临时限定（「这次只算华东区」）**不进这张表**，也不进长期记忆——
    它本来就是当轮对话的上下文，留在滑动窗口和摘要里就够了。把它当长期记忆
    存，正是旧实现里池子被撑满的直接原因。

  三层没有一层是个人维度，所以「不做用户画像」和「不会串记忆」这两个结论
  依然成立——但它现在是由一套机制撑住的，不再靠「口径天然是全局规则」这个假设。

生命周期按作用域分片，不是全局一刀切（旧实现是一个池子 + 一套 200 条上限）：
  - 正式口径：不参与 LRU、不过期，由治理流程管——「年终奖口径」一年用一次，
    按访问频率必然被淘汰，可它恰恰是最重要的那类。
  - 部门口径：在部门配额内做 LRU，超出时**降级为候选**而不是直接删，可恢复。
  - 候选口径：独立配额 + 停滞期，池子空不出来就删最久没人理的。
"""

from sqlalchemy import Column, Date, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

from app.database import Base
from app.models.doc_chunk import EMBEDDING_DIM

# ── 作用域 ──────────────────────────────────────────────────────────
SCOPE_GLOBAL = "global"           # 公司级：由制度定义、全公司生效
SCOPE_DEPARTMENT = "department"   # 部门级：只在本部门语境下生效
# 会话级（SCOPE_SESSION）不是这里的取值——它根本不落库，见模块 docstring。

# ── 状态 ────────────────────────────────────────────────────────────
# 「用」和「信」分开：用户说出来的口径当次就按他说的算（候选态在它自己的会话里
# 就能召回），但要推广给其他人得先过审核。审核决定的是「要不要推广出去」，
# 不是「这次算不算数」。
STATUS_CANDIDATE = "candidate"    # 用户交代、未经审核
STATUS_ACTIVE = "active"          # 已生效

# ── 配额（按作用域分片）─────────────────────────────────────────────
# 这三个常量取代了旧实现那个 `MAX_CONVENTIONS = 200` 全局上限。
# 那个上限正是「用一个池子管所有东西」的产物：它既让会话级临时限定挤掉真规则，
# 又让权威口径和临时约定共用一套 LRU。正式口径现在不设条数上限——它由治理流程管，
# 本来就是几十条量级，而且有审核闸门天然受控。
CANDIDATE_QUOTA = 100   # 候选池总条数，超出删最久未访问的
DEPT_QUOTA = 50         # 单个部门的口径条数，超出降级为候选（不删）
STALE_DAYS = 90         # 候选的停滞期：超过这么多天没人访问就清掉（只作用于候选）


class BusinessConvention(Base):
    """业务口径：按作用域隔离、候选/生效两态、改口版本化、分池淘汰。"""

    __tablename__ = "business_conventions"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="代理主键")

    content = Column(Text, nullable=False, comment="口径文本")
    embedding = Column(
        Vector(EMBEDDING_DIM), nullable=False, comment="口径向量（已 L2 归一化）"
    )

    scope = Column(
        String(16),
        nullable=False,
        server_default=SCOPE_GLOBAL,
        comment="作用域：global 公司级 / department 部门级",
    )
    # 提升成一等列，不再塞在 metadata 里：召回要按它过滤、淘汰要按它分片，
    # 藏在 JSONB 里做不了这两件事，只能把整表拉进 Python 再筛。
    department = Column(
        String(64), nullable=True, comment="部门作用域；scope=department 时必填"
    )
    status = Column(
        String(16),
        nullable=False,
        server_default=STATUS_CANDIDATE,
        comment="candidate 候选 / active 已生效",
    )
    # 候选口径「本会话立即生效」的依据：候选只在它诞生的那个会话里召回，
    # 审核通过（转 active）之后才对所有会话可见。
    session_id = Column(
        String(64), nullable=True, comment="候选口径的来源会话；转 active 后可置空"
    )

    # 有效期：口径会变，「2026 年起加班口径从 3 小时改成 2 小时」时不是删旧口径，
    # 是给旧的设终止日期、再起一个新版本。因为企业数据问答里大量是历史问题——
    # 去年 11 月的异常率要用去年 11 月的口径算，覆盖掉就永久还原不出来了。
    # NULL 一律表示「那一侧不设边界」：from 为空 = 一直有效，until 为空 = 当前版本。
    effective_from = Column(
        Date, nullable=True, comment="生效起始日；NULL=不设下界"
    )
    effective_until = Column(
        Date, nullable=True, comment="失效日；NULL=仍是当前版本"
    )

    # 调用方附加的任意字段（source 等），结构自由。
    # 列名留 metadata（对外契约），属性名用 meta —— metadata 在 SQLAlchemy
    # 声明式基类上是保留名，直接叫 metadata 会抛 InvalidRequestError。
    meta = Column(
        "metadata", JSONB, nullable=False, default=dict, comment="调用方附加字段，结构自由"
    )

    # 时序列独立成列（旧实现塞在 metadata 里）。这样「改口覆盖」的 UPDATE
    # 根本不碰 meta，调用方写入的 source 就不可能被静默抹掉。
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="首次写入时间"
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本版本改写时间",
    )
    last_accessed = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="最近一次被召回的时间；部门口径与候选池的 LRU 依据",
    )

    __table_args__ = (
        Index("idx_conventions_last_accessed", "last_accessed"),
        # 召回要按 (scope, department) 筛，淘汰要按 department 分组——这两个
        # 谓词各自有索引，不用把整张表拉进 Python。
        Index("idx_conventions_scope_dept", "scope", "department"),
        Index("idx_conventions_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<BusinessConvention(id={self.id}, scope={self.scope}, "
            f"status={self.status}, content='{self.content[:20]}')>"
        )

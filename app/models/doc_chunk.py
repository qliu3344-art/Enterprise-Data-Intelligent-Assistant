"""制度文档 chunk 及其向量（pgvector）。

向量和业务数据存在同一个 PostgreSQL 库里，不另起一套向量存储。两条理由：

1. 元数据过滤是原生的 —— doc_title / chapter / effective_date 就是普通列，
   WHERE 过滤、跟业务表 join 都是原生 SQL，不用翻译成向量库自己的过滤语法。
2. 同库同事务 —— 制度改版时「删旧 chunk、插新 chunk、更新向量」在同一个
   BEGIN / COMMIT 之间完成。跨库做不到这件事，而它失败的形态是静默的：
   业务表已经是新规、向量表还是旧向量，检索命中的是已作废条款，
   答案读起来完全通顺，没有一处报错。
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.database import Base

# BGE-small-zh-v1.5 的输出维度
EMBEDDING_DIM = 512


class DocChunk(Base):
    """制度条款分块 + 向量。"""

    __tablename__ = "doc_chunks"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="代理主键")

    # 文档内的稳定身份：增量重建按 (file_name, chunk_index) 对齐
    file_name = Column(String(255), nullable=False, comment="源文件名（含扩展名）")
    chunk_index = Column(Integer, nullable=False, comment="文档内 chunk 序号，从 0 起")

    doc_title = Column(String(255), nullable=False, comment="文档标题")
    chapter = Column(String(255), default="", comment="所属章（## 二级标题）")
    section = Column(String(255), default="", comment="所属节（### 三级标题）")

    content = Column(Text, nullable=False, comment="chunk 原文")
    content_hash = Column(
        String(32),
        nullable=False,
        comment="content 的 md5；增量重建先比哈希，未变则复用旧向量、不重新向量化",
    )

    embedding = Column(
        Vector(EMBEDDING_DIM),
        nullable=False,
        comment="BGE-small-zh-v1.5 向量（已 L2 归一化，走 cosine 距离）",
    )

    effective_date = Column(
        Date,
        comment="生效日期；NULL 表示长期有效（不是「未生效」）",
    )

    created_at = Column(DateTime, default=datetime.now, comment="该条款首次被索引的时间")
    updated_at = Column(
        DateTime, default=datetime.now, comment="该条款内容最近一次真正变化的时间"
    )

    __table_args__ = (
        UniqueConstraint("file_name", "chunk_index", name="uq_doc_chunks_file_chunk"),
        Index("idx_doc_chunks_doc_title", "doc_title"),
        Index("idx_doc_chunks_effective_date", "effective_date"),
        # 故意不建向量索引。1500 条 × 512 维顺序扫描是亚毫秒级，而近似索引
        # 要拿真实 Top-K 去换这点感知不到的提速——损失还是静默的：融合吃的是
        # 排名，掉一个真实邻居就会改 RRF 结果。且每次重建都要维护图，成本
        # 加在写路径上。库涨到十万级、或 p95 超过 50ms 再加：
        #   CREATE INDEX idx_doc_chunks_embedding ON doc_chunks
        #     USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
        # opclass 必须是 vector_cosine_ops，否则匹配不上 <=> 算子，
        # 不报错、只是静默退化成顺序扫描。
    )

    def __repr__(self) -> str:
        return f"<DocChunk(id={self.id}, file='{self.file_name}', idx={self.chunk_index})>"


class DocIndexState(Base):
    """每份源文档的索引状态。替代旧版 data/chroma_db/.docs_checksum 边车文件。

    这份状态无法从 doc_chunks 推导：文档级 md5 覆盖「含 frontmatter 的整份
    文件」，而 frontmatter 在分块前就被剥掉、不出现在任何 chunk 里。只改一个
    「版本号」会改文档哈希、却改零个 chunk 哈希——而那正是制度改版的宣告方式。

    放进同一张库的同一事务是必要的：旧实现把 checksum 写在库外的文件里，
    增量重建中途抛异常就会出现「向量已删、checksum 还是旧的」，下次启动
    据此判定「没变」，这份文档从此再也不会被索引。
    """

    __tablename__ = "doc_index_state"

    file_name = Column(String(255), primary_key=True, comment="源文件名")
    content_hash = Column(
        String(32), nullable=False, comment="整份文件（含 frontmatter）的 md5"
    )
    indexed_at = Column(DateTime, default=datetime.now, comment="最近一次重建时间")

    def __repr__(self) -> str:
        return f"<DocIndexState(file='{self.file_name}')>"

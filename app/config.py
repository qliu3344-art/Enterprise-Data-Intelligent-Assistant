import os
from pathlib import Path

# 加载 .env 文件（若存在）
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


class Settings:
    PROJECT_NAME: str = "企业数据智能助手"
    VERSION: str = "1.0.0"

    # PostgreSQL —— 业务数据与向量同库（向量走 pgvector 扩展，不另起一套存储）
    PG_HOST: str = "localhost"
    PG_PORT: int = 5432
    PG_USER: str = "postgres"
    PG_PASSWORD: str = os.getenv("PG_PASSWORD", "")
    PG_DB: str = "data_processing_platform"
    # LangGraph 会话检查点单独一个库：它是每轮都写的高频小事务，业务查询是慢查询，
    # 放同一个库里会互相拖累。
    CHECKPOINT_DB: str = "agent_checkpoints"

    @property
    def DATABASE_URL(self) -> str:
        # psycopg 3（postgresql+psycopg），不是 psycopg2
        from urllib.parse import quote_plus

        # 口令可能含 @ : / 等 URL 保留字符，不转义会把连接串拆错
        return (
            f"postgresql+psycopg://{self.PG_USER}:{quote_plus(self.PG_PASSWORD)}"
            f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}"
        )

    @property
    def CHECKPOINT_DB_URL(self) -> str:
        """LangGraph checkpointer 的连接串（psycopg 直连，不经 SQLAlchemy）。

        用 make_conninfo 而不是拼字符串：口令里的空格、引号、反斜杠要按
        libpq 的规则转义，手拼必错。
        """
        from psycopg.conninfo import make_conninfo

        return make_conninfo(
            host=self.PG_HOST,
            port=self.PG_PORT,
            user=self.PG_USER,
            password=self.PG_PASSWORD,
            dbname=self.CHECKPOINT_DB,
        )

    # DashScope (通义千问)
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    # 主力模型：路由之外的全部 LLM 调用——Agent 多轮工具调用、RAG 答案合成、
    # 表头对齐、清洗判异、口径冲突确认、上下文压缩。这些活的共同点是「要的是
    # 答案质量」，qwen-plus 是效果 / 速度 / 成本三者均衡的那一档。
    LLM_MODEL: str = "qwen-plus"

    # 路由模型：只给意图路由的 logprobs 调用用。
    # 和主力分开，是因为两者优化的东西不一样：路由要的不是「回答得多好」，
    # 是「分布给得多完整」。三选一分类本身不需要更大的模型，而 logprobs 的候选
    # 丰富度是硬约束——实测更大的一档会把 top_logprobs 忽略掉、只回 1 个候选，
    # margin 和 label_mass 一起退化成常量，不报错，整套诊断能力静默归零。
    # 另外路由是每次查询都跑的高频调用，主力只在链路最后跑一次。
    LLM_MODEL_ROUTER: str = "qwen-flash"

    # 裁判模型：只给 eval.py 里的两个 LLM-as-Judge 用（答案质量评分、检索相关性判定）。
    # 必须和生成答案的模型不同——同源裁判会给自己人的输出打高分，分数涨的是
    # 自我偏好不是质量，而这条偏差在看单次报告时完全不可见。
    # 选 plus 是 2026-09-30 三档 100 条 × 3 遍实测的结果：flash 会把「原样复述问题」
    # 这种明显错的答案打成 3.56/5（判词里自己都写了「仅重复问题」），放过了退化；
    # max 虽然判得准，但同一份答案重打三遍只有 34% 一致——尺子自己就不稳，比不准更糟。
    # ⚠️ 换这个值等于换尺子：answer_quality / rag_semantic 的历史分数不可比。
    LLM_MODEL_JUDGE: str = "qwen-plus"

    # 单次 LLM 调用的读超时（秒）。和路由那条预算不是一回事：路由有 L2 兜底、
    # 必须卡在 1 秒内；这一条没有兜底，是用户真正在等的那一次，得给得宽。
    # 60 的判据是「实测单次调用 1~30 秒」留一倍余量。
    #
    # 为什么必须显式设：DashScope SDK 的默认值是 300 秒，网络抖一下就把一个请求
    # 挂满 5 分钟（2026-10-06 跑 400 条评测时被咬掉 6 条、多花约 20 分钟）。
    # ⚠️ 它和路由那处是**两个独立客户端、同一个 300 秒默认值**：
    # 那边改的是 `dashscope.Generation.call` 的 request_timeout，这边是
    # ChatTongyi（经 model_kwargs 透传）。改一处不会带上另一处，两边都要写。
    LLM_REQUEST_TIMEOUT_SECONDS: int = 60

    # 意图标签单 token 校验用的 tokenizer（须与线上服务的分词器同族）。
    # 也可以指向本地目录，离线环境预下载后改这里即可。
    INTENT_TOKENIZER: str = os.getenv("INTENT_TOKENIZER", "Qwen/Qwen2.5-1.5B-Instruct")

    # 文件存储
    UPLOAD_DIR: str = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "uploads"
    )
    EXPORT_DIR: str = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "exports"
    )

    # 日志
    LOG_DIR: str = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "logs"
    )
    LOG_LEVEL: str = "INFO"


settings = Settings()

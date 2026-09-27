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

    # MySQL
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DB: str = "data_processing_platform"

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DB}?charset=utf8mb4"
        )

    # DashScope (通义千问)
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    # qwen-flash：qwen-turbo 的官方继任者——百炼文档已明示 qwen-turbo 不再更新、
    # 建议迁移到 qwen-flash。选它的依据是本项目的调用结构：输入以长 prompt 为主
    # （Agent 历史、清洗批次候选、RAG 文档块），所以输入单价是主成本杠杆。
    # 输入 0.15 元/百万（turbo 0.367），输出 1.5（turbo 1.468），缓存命中输入仅 0.03。
    # 路由与主力共用同一个模型：已实测它在 logprobs 与 function calling 上都不输 turbo。
    LLM_MODEL: str = "qwen-flash"

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

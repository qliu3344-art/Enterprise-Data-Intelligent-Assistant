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
    PROJECT_NAME: str = "自动化客户数据处理平台"
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
    LLM_MODEL: str = "qwen-turbo"

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

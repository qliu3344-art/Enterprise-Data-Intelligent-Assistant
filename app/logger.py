import logging
import os
from logging.handlers import RotatingFileHandler

from app.config import settings


def setup_logger(name: str = "data_platform") -> logging.Logger:
    """配置应用日志：控制台 + 按大小轮转的文件日志。"""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 文件输出（10 MB 轮转，保留 5 个历史文件）
    os.makedirs(settings.LOG_DIR, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(settings.LOG_DIR, "app.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()

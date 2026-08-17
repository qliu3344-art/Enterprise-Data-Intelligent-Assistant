"""LLM 调用容错重试 — 指数退避 + 可重试/不可重试分类。

可重试：超时、连接失败、限流(429)、临时 5xx —— 重试有意义
不可重试：参数错误、业务拒绝、越权 —— 直接抛出，重试只会重复失败
"""

import time

from app.logger import logger

# 可重试错误的关键词（匹配异常信息字符串）
_RETRYABLE_KEYWORDS = (
    "timeout", "timed out", "connection", "connect", "reset by peer",
    "429", "rate limit", "too many requests", "throttl",
    "500", "502", "503", "service unavailable", "overload", "temporar",
)


def is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试。"""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in _RETRYABLE_KEYWORDS)


def retry_call(fn, *args, max_retries: int = 3, base_delay: float = 1.0, **kwargs):
    """执行 fn，遇到可重试异常按指数退避重试。

    Args:
        fn: 要执行的函数（如 Generation.call / llm.invoke）
        max_retries: 总尝试次数（含首次）
        base_delay: 首次退避秒数，之后每次翻倍（1s → 2s → 4s）

    不可重试的异常直接抛出，不做无意义重试。
    """
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if not is_retryable(e) or attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"LLM 调用失败（可重试），{delay:.1f}s 后重试 "
                f"[{attempt + 1}/{max_retries - 1}]: {e}"
            )
            time.sleep(delay)

"""LLM 调用容错重试 — 指数退避 + 随机抖动 + 可重试/不可重试分类。

可重试：超时、连接失败、限流(429)、临时 5xx —— 重试有意义
不可重试：400（参数错，重试一万次结果一样）、401（鉴权失效，重试是徒劳）

重试预算由端到端延迟反推，不拍脑袋定「重试 3 次」：
  用户可接受的 P99 约 3 秒，hybrid 安全网本身要 1.5~2 秒，
  留给意图路由的预算约 1 秒 —— 即单次 timeout 950ms、最多 2 次。
  超时预算用完就判定失败，进入 L2 规则降级。不能为了省一次 hybrid 的钱把整个
  请求拖死，那是典型的局部最优。

⚠ 这条线是量出来的，不是除出来的。一次托管 LLM 调用的延迟大头在
  「首 token 之前」（排队 + prefill），实测地板 2025 年定这条时约 300ms；
  max_tokens=1 省的是首 token 之后那一段，压不动这个地板。所以单次配额必须高于
  地板，否则每次调用都会在超时处被掐断，L2 从兜底变成默认——整个 logprobs 方案
  等于没上。

  ⚠️ 2026-10-07~09 这句话被原样验证了一遍：端点地板漂到约 490ms（三档模型一致），
  正好压在当时的 500ms 配额上，降级率从 3.5% 涨到 37%。地板是会动的，配额得跟着抬。

⚠ 超时不重试。预算内没返回，说明这个端点此刻就是慢，重试只是在同一个慢端点上
  再等一整个预算、把请求拖成两倍（实测 500+20+500 ≈ 1020ms）。只有「快速失败」
  ——连不上、429、5xx——才值得换一次，那种失败通常毫秒级返回，重试不占预算。
"""

import random
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError

from app.logger import logger

# 意图路由的重试预算 —— 常量，由上面那段推导定死，调用点不允许随手改
ROUTE_TIMEOUT_SECONDS = 0.95  # 单次调用超时；地板 2026-10 实测约 490ms，留出余量
ROUTE_MAX_ATTEMPTS = 2  # 含首次；只对「快速失败」生效，超时不重试（见 RouteBudgetTimeout）
ROUTE_BASE_DELAY = 0.02

# 可重试错误的关键词（匹配异常信息字符串）
_RETRYABLE_KEYWORDS = (
    "timeout", "timed out", "connection", "connect", "reset by peer",
    "429", "rate limit", "too many requests", "throttl",
    "500", "502", "503", "service unavailable", "overload", "temporar",
)

# 退避抖动比例：实际退避在 ±30% 内随机，避免限流恢复的瞬间所有请求同时打过去
_JITTER_RATIO = 0.3

_executor: ThreadPoolExecutor | None = None


class RouteBudgetTimeout(TimeoutError):
    """路由自己的超时预算用完了 —— 和 requests 那类网络超时不是一回事。

    网络超时（连不上、读不到）值得换个连接重试；这个不值得：调用已经在一个慢
    端点上耗掉了整个预算，再等一个预算只是把请求拖成两倍。这条区分是从 2026-10
    那次端点变慢里来的——地板压到配额上时，重试从「补救」变成了「延迟翻倍」。
    """


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="retry-call")
    return _executor


def is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试。"""
    if isinstance(exc, RouteBudgetTimeout):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in _RETRYABLE_KEYWORDS)


def _call_with_timeout(fn, args: tuple, kwargs: dict, timeout: float):
    """在独立线程里执行 fn 并施加超时。

    超时后线程无法被强杀（Python 没有安全的线程中断），它会自己跑完、结果丢弃。
    对亚秒级的预算来说这个代价可以接受——换来的是「超时预算用完就判失败」
    这条规则真的生效，而不是让请求慢慢拖死。
    """
    future = _get_executor().submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeoutError:
        future.cancel()
        raise RouteBudgetTimeout(f"单次调用超过 {timeout}s 预算")


def retry_call(
    fn,
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    timeout: float | None = None,
    **kwargs,
):
    """执行 fn，遇到可重试异常按指数退避 + 随机抖动重试。

    Args:
        fn: 要执行的函数（如 Generation.call / llm.invoke）
        max_retries: 总尝试次数（含首次）
        base_delay: 首次退避秒数，之后每次翻倍
        timeout: 单次调用的超时预算（秒）；None 表示不额外施加超时

    不可重试的异常直接抛出，不做无意义重试。
    """
    for attempt in range(max_retries):
        try:
            if timeout is None:
                return fn(*args, **kwargs)
            return _call_with_timeout(fn, args, kwargs, timeout)
        except Exception as e:
            if not is_retryable(e) or attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            delay *= 1 + random.uniform(-_JITTER_RATIO, _JITTER_RATIO)
            logger.warning(
                f"LLM 调用失败（可重试），{delay:.2f}s 后重试 "
                f"[{attempt + 1}/{max_retries - 1}]: {e}"
            )
            time.sleep(delay)

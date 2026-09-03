"""上下文压缩：滑动窗口 + 摘要压缩。

多轮对话的「短 / 中」期记忆管理：
  - 滑动窗口（短期）：保留最近 recent_k 条消息原文 —— 多轮指代
    （"它"、"那个人"）必须靠最近原文才能消解。
  - 摘要压缩（中期）：窗口之外更早的历史，用 LLM 压成一句话摘要
    —— 保留结论、丢弃过程。

核心原则：按需注入，不是全量注入。配合 memory_store（业务口径记忆）组成
短 / 中 / 长三级上下文管理。
"""

from langchain_core.messages import HumanMessage, SystemMessage

from app.logger import logger

# 保留最近的消息条数（约 recent_k // 2 轮对话，每轮 user + assistant）
RECENT_K = 8

# 摘要生成 Prompt
SUMMARY_PROMPT = """请把下面这段对话历史压缩成一句话摘要，保留关键结论和事实，丢弃过程细节。

要求：
1. 只输出摘要本身，不要任何解释或前缀
2. 保留具体数字、结论、用户关注的重点
3. 用中文，不超过 80 字

对话历史：
{transcript}

摘要："""


def _messages_to_text(messages) -> str:
    """把消息列表转成可读文本。"""
    parts = []
    for m in messages:
        role = "用户" if isinstance(m, HumanMessage) else "助手"
        content = m.content if isinstance(m.content, str) else str(m.content)
        if content:
            parts.append(f"{role}：{content[:500]}")
    return "\n".join(parts)


def summarize_messages(messages) -> str:
    """用 LLM 将历史消息压成一句话摘要。

    LLM 失败时降级为原文截断，保证主流程不中断。
    """
    if not messages:
        return ""

    transcript = _messages_to_text(messages)
    if not transcript.strip():
        return ""

    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_core.messages import HumanMessage as HM

    from app.config import settings

    prompt = SUMMARY_PROMPT.format(transcript=transcript[:3000])
    try:
        llm = ChatTongyi(
            model=settings.LLM_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.0,
            max_tokens=200,
        )
        resp = llm.invoke([HM(content=prompt)])
        summary = resp.content.strip()
        if summary:
            logger.info(f"历史已压缩为摘要: {summary[:60]}...")
            return summary
    except Exception as e:
        logger.warning(f"摘要生成失败，降级为原文截断: {e}")

    # 降级：直接截断原文前 200 字
    return transcript[:200] + "……"


def compress_history(history, recent_k: int = RECENT_K):
    """滑动窗口 + 摘要压缩。

    Args:
        history: 历史消息列表（LangChain message 对象）
        recent_k: 保留最近的消息条数

    Returns:
        压缩后的消息列表。len <= recent_k 时原样返回；
        否则返回 [SystemMessage(摘要)] + 最近 recent_k 条原文。
    """
    history = list(history)
    if len(history) <= recent_k:
        return history

    recent = history[-recent_k:]
    older = history[:-recent_k]

    summary = summarize_messages(older)
    if summary:
        return [SystemMessage(content=f"[历史摘要] {summary}")] + recent
    return recent

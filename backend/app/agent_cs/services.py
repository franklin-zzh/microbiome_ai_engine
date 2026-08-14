"""会话状态机管理模块（v2 核心）

Redis 键设计：
    session:{session_id}:state        -> NORMAL / HUMAN_MODE / BLOCKED（TTL=session_ttl_seconds）
    session:{session_id}:neg_streak   -> 连续负面情绪计数（TTL 同会话）
    session:{session_id}:context      -> JSON 上下文快照（最近 N 轮摘要）

状态流转：
    NORMAL --(输入"转人工" 或 连续负面情绪>=阈值)--> HUMAN_MODE
    NORMAL --(命中高风险医疗关键词)--> BLOCKED
    HUMAN_MODE/BLOCKED --(人工接管完成/管理员重置)--> NORMAL

运行时以 Redis 为准；session_state 表为持久化镜像，Redis 丢失后由表重建。
"""
import json
from typing import Optional

from redis import Redis

from app.core.config import get_settings
from app.core.logging import structured_log
from app.core.redis import get_redis

settings = get_settings()

# 会话状态常量
STATE_NORMAL = "NORMAL"
STATE_HUMAN_MODE = "HUMAN_MODE"
STATE_BLOCKED = "BLOCKED"

# 触发词（HITL）
HUMAN_REQUEST_KEYWORDS = ("转人工", "人工客服", "找人工", "真人", "客服电话", "投诉")
# 高风险医疗关键词（与 .agent/rules/medical-safety.md 对齐；第一道拦截，0 LLM 成本。
# 含工作流 risk_guard 提示词 Risk Taxonomy 中的确定性词：柏油样便/呕血/频繁呕吐等）
RISK_KEYWORDS = (
    "便血", "黑便", "鲜血便", "柏油样便", "大便带血", "呕血",
    "剧烈腹痛", "持续腹痛", "绞痛", "肚子疼到打滚", "无法直立",
    "持续腹泻", "水样便", "频繁呕吐", "脓血便", "体重骤降", "不明原因消瘦",
    "肠梗阻", "肠穿孔", "肠癌", "直肠癌", "结肠癌", "高烧不退", "严重脱水",
)

SESSION_STATE_KEY = "session:{session_id}:state"
SESSION_NEG_KEY = "session:{session_id}:neg_streak"
SESSION_CTX_KEY = "session:{session_id}:context"
# Dify 对话 conversation_id（多轮续聊用，与状态机共用会话 TTL）
SESSION_DIFY_CONV_KEY = "session:{session_id}:dify_conv"


def _ttl() -> int:
    return settings.session_ttl_seconds


def get_state(redis: Redis, session_id: str) -> str:
    """读取当前会话状态（Redis 缺失时回退 NORMAL）"""
    value = redis.get(SESSION_STATE_KEY.format(session_id=session_id))
    return value if value in (STATE_NORMAL, STATE_HUMAN_MODE, STATE_BLOCKED) else STATE_NORMAL


def set_state(redis: Redis, session_id: str, state: str, ttl: Optional[int] = None) -> str:
    """写入会话状态并刷新 TTL"""
    redis.set(SESSION_STATE_KEY.format(session_id=session_id), state, ex=ttl or _ttl())
    return state


def increment_negative_streak(redis: Redis, session_id: str, ttl: Optional[int] = None) -> int:
    """连续负面情绪计数 +1，返回当前计数（ttl 显式传入时覆盖默认会话 TTL）"""
    key = SESSION_NEG_KEY.format(session_id=session_id)
    count = redis.incr(key)
    redis.expire(key, ttl or _ttl())
    return count


def reset_negative_streak(redis: Redis, session_id: str) -> None:
    redis.delete(SESSION_NEG_KEY.format(session_id=session_id))


def should_answer(redis: Redis, session_id: str) -> bool:
    """HITL 判断：HUMAN_MODE / BLOCKED 下 AI 停止抢答"""
    return get_state(redis, session_id) == STATE_NORMAL


def mark_human(redis: Redis, session_id: str, reason: str = "user_request", ttl: Optional[int] = None) -> str:
    """用户请求转人工 / 连续负面情绪 -> HUMAN_MODE"""
    state = set_state(redis, session_id, STATE_HUMAN_MODE, ttl=ttl)
    structured_log(
        event="session_mark_human",
        status=STATE_HUMAN_MODE,
        extra={"session_id": session_id, "reason": reason},
    )
    return state


def mark_blocked(redis: Redis, session_id: str, reason: str = "risk_keyword", ttl: Optional[int] = None) -> str:
    """命中高风险医疗关键词 -> BLOCKED"""
    state = set_state(redis, session_id, STATE_BLOCKED, ttl=ttl)
    structured_log(
        event="session_mark_blocked",
        status=STATE_BLOCKED,
        extra={"session_id": session_id, "reason": reason},
    )
    return state


def release_human(redis: Redis, session_id: str, ttl: Optional[int] = None) -> str:
    """人工接管完成 / 管理员重置 -> 回 NORMAL"""
    state = set_state(redis, session_id, STATE_NORMAL, ttl=ttl)
    reset_negative_streak(redis, session_id)
    structured_log(
        event="session_release_human",
        status=STATE_NORMAL,
        extra={"session_id": session_id},
    )
    return state


def contains_keyword(text: str, keywords: tuple) -> Optional[str]:
    """命中返回第一个关键词，否则 None"""
    for kw in keywords:
        if kw in text:
            return kw
    return None


def route_incoming(redis: Redis, session_id: str, user_message: str) -> dict:
    """入站消息统一分流：返回该消息应走的处理路径

    优先级（医疗风控 > 转人工诉求）：
        BLOCKED 状态（保持熔断）> 命中高风险医疗关键词 -> BLOCKED
        > 命中转人工关键词 / 已是 HUMAN_MODE -> HUMAN_MODE
        > 其余 -> NORMAL（AI 正常作答）

    Returns:
        {
          "state": NORMAL/HUMAN_MODE/BLOCKED,
          "hit_keyword": 命中的关键词或 None,
          "negative_streak": 连续负面计数,
          "should_answer": bool
        }
    """
    state = get_state(redis, session_id)

    # 已 BLOCKED：保持熔断，不参与后续任何分流
    if state == STATE_BLOCKED:
        return {"state": state, "hit_keyword": None, "negative_streak": 0, "should_answer": False}

    # 高风险医疗关键词（最高优先级，覆盖转人工诉求；HUMAN_MODE 下也升级为 BLOCKED）
    risk_kw = contains_keyword(user_message, RISK_KEYWORDS)
    if risk_kw:
        mark_blocked(redis, session_id, reason=f"risk_keyword:{risk_kw}")
        return {"state": STATE_BLOCKED, "hit_keyword": risk_kw, "negative_streak": 0, "should_answer": False}

    # 转人工关键词 / 已是 HUMAN_MODE
    human_kw = contains_keyword(user_message, HUMAN_REQUEST_KEYWORDS)
    if human_kw or state == STATE_HUMAN_MODE:
        mark_human(redis, session_id, reason=f"keyword:{human_kw}" if human_kw else "already_human")
        return {"state": STATE_HUMAN_MODE, "hit_keyword": human_kw, "negative_streak": 0, "should_answer": False}

    return {"state": state, "hit_keyword": None, "negative_streak": 0, "should_answer": True}


def snapshot_context(redis: Redis, session_id: str, context: dict, ttl: Optional[int] = None) -> None:
    """保存上下文快照（异步任务消费后写入；ttl 显式传入时覆盖默认会话 TTL）"""
    redis.set(
        SESSION_CTX_KEY.format(session_id=session_id),
        json.dumps(context, ensure_ascii=False),
        ex=ttl or _ttl(),
    )


def get_context(redis: Redis, session_id: str) -> Optional[dict]:
    raw = redis.get(SESSION_CTX_KEY.format(session_id=session_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def get_dify_conversation(redis: Redis, session_id: str) -> Optional[str]:
    """读取 Dify 会话 ID（续聊用；无则 None = 新会话）"""
    return redis.get(SESSION_DIFY_CONV_KEY.format(session_id=session_id))


def set_dify_conversation(redis: Redis, session_id: str, conversation_id: str, ttl: Optional[int] = None) -> None:
    """保存 Dify 会话 ID（TTL 与状态机一致，随会话过期）"""
    redis.set(
        SESSION_DIFY_CONV_KEY.format(session_id=session_id),
        conversation_id,
        ex=ttl or _ttl(),
    )


def cleanup_expired(redis: Redis, session_id: str) -> None:
    """会话过期清理（可在会话结束时调用）"""
    redis.delete(
        SESSION_STATE_KEY.format(session_id=session_id),
        SESSION_NEG_KEY.format(session_id=session_id),
        SESSION_CTX_KEY.format(session_id=session_id),
        SESSION_DIFY_CONV_KEY.format(session_id=session_id),
    )

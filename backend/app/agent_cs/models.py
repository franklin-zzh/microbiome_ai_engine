import enum
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)

from app.core.database import Base


class ChatChannel(str, enum.Enum):
    """四类微信入口渠道"""
    WXKF = "WXKF"              # 企微「微信客服」单聊
    MP = "MP"                  # 公众号 / 服务号
    H5 = "H5"                  # 官网聊天窗口（自建 H5）
    WECOM_GROUP = "WECOM_GROUP"  # 企微群机器人


class SessionStateValue(str, enum.Enum):
    NORMAL = "NORMAL"
    HUMAN_MODE = "HUMAN_MODE"
    BLOCKED = "BLOCKED"


class CsChatLog(Base):
    """全量对话日志湖：每一条用户消息与 AI 回复必须落库（mb_ai_engine.cs_chat_logs）"""
    __tablename__ = "cs_chat_logs"

    id = Column(Integer, primary_key=True, index=True)
    channel = Column(Enum(ChatChannel, name="chat_channel"), nullable=False)
    session_id = Column(String(128), nullable=False, index=True)
    open_id = Column(String(128), nullable=False, index=True)

    user_message = Column(Text, nullable=False)
    ai_reply = Column(Text)
    retrieved_chunks = Column(JSON)          # Dify RAG 召回切片（含文本与得分）
    intent = Column(String(64))              # FAQ / PRODUCT / PROCEDURE / MARKETING / UNKNOWN

    match_score = Column(Numeric(5, 4))      # 最佳召回得分（<0.65 触发缺口捕获）
    satisfaction = Column(SmallInteger)      # 满意度 1-5，NULL = 未收集
    hit_human = Column(Boolean, nullable=False, default=False)
    human_takeover = Column(String(128))     # 接管人企微 UserId

    risk_flag = Column(Boolean, nullable=False, default=False)
    meta = Column(JSON)                      # 扩展元数据

    created_at = Column(DateTime, server_default=func.now(), index=True)


class SessionState(Base):
    """会话状态表：Redis 状态机的持久化镜像（mb_ai_engine.cs_session_state）"""
    __tablename__ = "cs_session_state"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(128), nullable=False, unique=True, index=True)
    channel = Column(Enum(ChatChannel, name="chat_channel"), nullable=False)
    open_id = Column(String(128), nullable=False, index=True)

    state = Column(Enum(SessionStateValue, name="session_state_value"),
                   nullable=False, default=SessionStateValue.NORMAL)
    context = Column(JSON)                   # 上下文快照（最近 N 轮摘要）
    negative_streak = Column(SmallInteger, nullable=False, default=0)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    expires_at = Column(DateTime)  # 与 Redis TTL 对齐

    def mark_human(self, reason: str = "user_request") -> None:
        self.state = SessionStateValue.HUMAN_MODE

    def mark_blocked(self) -> None:
        self.state = SessionStateValue.BLOCKED

    def reset(self) -> None:
        self.state = SessionStateValue.NORMAL
        self.negative_streak = 0

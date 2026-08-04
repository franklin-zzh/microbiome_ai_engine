"""销售 Agent 业务域（Phase 2 预留）

表归属：mb_ai_cs.leads_preview（线索预备表，与客服会话体系同库，便于跨域分析）
"""
import enum

from sqlalchemy import JSON, Column, DateTime, Enum, Integer, Numeric, String, Text, func

from app.agent_cs.models import ChatChannel
from app.core.database import BaseCs


class LeadStatus(str, enum.Enum):
    NEW = "NEW"
    ASSIGNED = "ASSIGNED"
    CONVERTED = "CONVERTED"
    CLOSED = "CLOSED"


class LeadsPreview(BaseCs):
    """线索预备表：低置信度/高意向对话异步生成，供销售跟进（mb_ai_cs.leads_preview）"""
    __tablename__ = "leads_preview"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(128), nullable=False)
    open_id = Column(String(128), nullable=False, index=True)
    channel = Column(Enum(ChatChannel, name="chat_channel"), nullable=False)

    intent_tags = Column(JSON)               # 画像标签（购买意愿/关注重点/品类偏好）
    summary = Column(Text)                   # AI 生成的用户画像总结
    confidence = Column(Numeric(5, 4))       # 线索置信度
    status = Column(Enum(LeadStatus, name="lead_status"),
                    nullable=False, default=LeadStatus.NEW)
    assigned_to = Column(String(128))        # 跟进销售企微 UserId

    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

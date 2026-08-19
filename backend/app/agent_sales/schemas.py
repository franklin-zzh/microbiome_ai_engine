"""销售 Agent 业务域：线索与企微消息 Schema"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class LeadsPreviewOut(BaseModel):
    id: int
    session_id: str
    open_id: str
    channel: str
    intent_tags: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    confidence: Optional[float] = None
    status: str
    assigned_to: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WeComWebhookPushRequest(BaseModel):
    """通用 Webhook 消息推送请求"""
    msgtype: str = Field(default="text", description="消息类型: text, markdown, template_card")
    content: Optional[str] = Field(default=None, description="文本或 Markdown 内容")
    template_card: Optional[Dict[str, Any]] = Field(default=None, description="模版卡片 JSON 内容")
    mentioned_list: Optional[List[str]] = Field(default=None, description="@成员列表，如 ['@all']")
    webhook_url: Optional[str] = Field(default=None, description="可选指定群机器人 Webhook URL")


class WeComSalesIntroPushRequest(BaseModel):
    """富玛特招商介绍模板卡片推送请求"""
    title: str = Field(default="富玛特招商合作与加盟政策", description="卡片主标题")
    desc: str = Field(default="肠道微生态领军企业 · 诚邀城市合伙人与渠道分销商", description="卡片副标题")
    emphasis_title: str = Field(default="1000亿+", description="关键指标数据（如活菌量）")
    emphasis_desc: str = Field(default="高活性益生菌活菌量 (CFU/袋)", description="关键指标说明")
    quote_text: str = Field(
        default="国家高新技术企业 | GMP十万级无菌生产线 | 拥有一类/二类医疗器械及微生态专利矩阵",
        description="资质背书引用文本",
    )
    action_url: str = Field(default="https://www.fmtcloud.cn", description="点击卡片/按钮跳转链接")
    webhook_url: Optional[str] = Field(default=None, description="可选指定群机器人 Webhook URL")


class WeComLeadAlertPushRequest(BaseModel):
    """商机线索卡片推送请求"""
    client_name: str = Field(..., description="客户姓名或外部微信昵称")
    group_name: str = Field(..., description="来源企微群名称")
    intent_summary: str = Field(..., description="客户诉求与合作意向摘要")
    key_concerns: str = Field(default="商务价格 / 代理政策 / 签合同", description="关注重点")
    confidence: float = Field(default=0.95, description="商机置信度 (0~1)")
    webhook_url: Optional[str] = Field(default=None, description="内部销售群 Webhook URL")

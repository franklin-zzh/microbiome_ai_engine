"""销售 Agent 业务域：线索提取（Phase 2 预留）

异步分析 mb_ai_cs.cs_chat_logs（客服会话日志湖），按意图/购买意愿/品类偏好
沉淀用户画像，写入 mb_ai_cs.leads_preview 供销售跟进。

跨域边界：销售域对客服会话日志只读，不修改客服域数据；
写入目标为销售域自有表 leads_preview（同库 mb_ai_cs，不跨 schema）。
"""

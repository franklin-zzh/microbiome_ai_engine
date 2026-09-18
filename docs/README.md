# docs/ 文档索引

> 本目录收录 agent_cs（微信 AI 客服 + 知识库引擎）的架构、契约与运维文档。
> 主索引见根目录 `AGENT.md`（动态记忆区含最新上下文）。

| 文件 | 主要内容概览 |
|---|---|
| `ARCHITECTURE-REVIEW.md` | 架构审查报告（2026-08-05）：P0-P3 问题清单（安全/可靠性/演进/工程化）+ R1-R5 改进路线图 |
| `CS-WORKFLOW-BASELINE.md` | CS 客服工作流冒烟基线：三分支验证、双库绑定映射、契约实测、召回基线、发布后接口基线（速度/tokens） |
| `CS-ISSUE-LOG.md` | CS 客服 Agent 问题记录表：速度类（S1-S3）/效果类（E1-E7）/工程类（P1-P4）问题 + 整改优先级 |
| `cs-workflow-runbook.md` | cs workflow 跑通操作手册（Dify 控制台侧）：模型供应商、DSL 导入、节点核对、环境变量、种子数据、基线回填 |
| `wxkf-runbook.md` | 微信客服接入联调手册：链路架构、企微后台配置、frp 预检、三态（NORMAL/BLOCKED/HUMAN_MODE）端到端验证、故障排查表、收尾核对清单 |
| `DIFY-API-CONTRACT.md` | Dify Service API 契约（1.16.1 冻结版）：端点总览、create_by_text payload、异步索引行为、删除限制、待实测回填 |
| `ETL-NOTES.md` | 本地 ETL 清洗实测记录：各格式清洗效果矩阵、图片文字提取方案、7 条避坑清单、图片处理策略、入库 SOP |
| `WECOM-AIBOT-GUIDE.md` | 企业微信智能机器人接入指南：WebSocket（OpenWS）长连接协议、Dify 打字机流式输出、群聊/私聊双场景隔离、商机拦截与待办去重设计 |
| `smoke-seed/` | 冒烟种子素材：`cs_general_肠道菌群检测流程.md`（General 库干净 md）、`cs_qa_faq_种子.txt`（Q&A 库 FAQ 问答对） |

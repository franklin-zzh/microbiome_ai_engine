# 方案评审意见 · 微信 AI 客服设计方案 v2

> 评审日期：2026-08-03 ｜ 对象：`.agent/designs/wechat-ai-cs-v2.md`

## 一、总体评价：方案成熟、可落地，评分 8/10

整体设计具备工程可行性：双轨 RAG、异步解耦、数据闭环三大核心决策全部正确，与现有代码底座（FastAPI + PostgreSQL + Dify Sync）高度兼容。主要问题集中在"选型未定死"和"微信入口细节"上，不影响主路线。

## 二、亮点（保留）

1. **双轨 RAG（FAQ 高阈值 + Product 混合检索）**：FAQ 问答对用高匹配阈值保证"常见问题 100% 命中"，长尾问题降级到 Product KB 混合检索 + Rerank——比单一向量库更稳，且与现有 `core_unanswered_questions` 低分捕获机制衔接自然。
2. **5 秒异步回包设计正确**：微信/企微回调硬约束 5 秒内必须返回 `success`，方案用 Redis 缓存 + Celery 队列异步处理 + 完成后主动推送，这是官方 API 下的唯一正确做法；回调接口内严禁同步调 Dify。
3. **全量日志落库（Data Lake 闭环）**：`chat_logs` 沉淀上下文 + RAG 召回切片得分，直接支撑第 6 周"知识库待补充清单"反哺与用户画像分析——方案把数据当作资产而非垃圾，是最大亮点。
4. **合规与 HITL 齐备**：医疗免责声明、敏感词拦截、Prompt 注入防护、"转人工"状态机，医疗健康场景刚需。
5. **向量库务实**：直接随 Dify 自带部署，明确不维护 Milvus 集群，符合 MVP 轻量原则。

## 三、修正点（已在本项目落地时处理）

1. **存储选型定 MySQL 单库 `mb_ai_engine`**（方案原文"MySQL / PostgreSQL"二选一，业务方要求沿用 MySQL）：2026-08-03 定双库 `mb_ai_core` + `mb_ai_cs`，2026-08-04 合并为单库（`core_*` / `cs_*` 表名前缀隔离，跨领域不建物理外键；双库数据经 `scripts/migrate_single_db.py` RENAME 迁移，表结构由 Alembic 管理）；Dify 官方自带 `db_postgres` 仅作其元数据库（宿主端口 5434），向量库用 Weaviate（非 pgvector）。已写入 `AGENT.md`、设计稿与 `database-schema.md`。
2. **"微信客服"需按 channel 区分**：企微「微信客服」（单聊，主动推送受 48h 会话窗口限制）、公众号/服务号（被动回复 + 客服消息，两套不同 API）、H5 官网聊天窗、企微群机器人（Webhook，无状态）是**四类不同接入**。表结构统一加 `channel` 枚举字段（`WXKF / MP / H5 / WECOM_GROUP`），回调路由按 channel 分派。群机器人按群维度建 session，非 @ 消息静默。
3. **Dify 部署方式**：Dify 官方 compose 极其庞大（api/worker/web/db/redis/sandbox/ssrf_proxy/weaviate/nginx 等，当前版本 1.16.1），不适合塞进主 docker-compose。落地为：主 `docker-compose.yml` 只管理网关栈（PostgreSQL+Redis+backend），另立 `docker-compose.dify.yml` 部署 Dify（官方镜像，端口不冲突）。
4. **第一周不引入 Celery**：方案本身把 Celery 放在第 3 周 Task 3.2，第 1 周仅需 Redis 状态机 + 缓存能力。保持该节奏，避免过早引入任务队列复杂度。
5. **满意度收集机制需明确定义**：方案只写"收集客户满意度"。建议 MVP 用会话结束后的评价卡片/链接回传，`chat_logs.satisfaction` 预留字段（详见 database-schema.md）。

## 四、待确认事项（需业务侧拍板，不影响第 1 周开发）

| # | 事项 | 影响 |
|---|---|---|
| 1 | 企微微信客服 App 的 CorpID/Secret/Token/EncodingAESKey 是否已申请 | 第 3 周联调前提 |
| 2 | 公众号为订阅号还是服务号（决定客服消息接口可用性） | 第 3 周入口实现 |
| 3 | Dify 的 Embedding 与 LLM（DeepSeek/Qwen）API Key 由哪方提供 | 第 1 周 Task 1.1 配置 |
| 4 | 满意度收集交互形态（评价卡片 / 链接 / 关键词） | 第 5 周落地 |
| 5 | 企微群机器人 Webhook 地址与群范围 | 第 3 周接入 |

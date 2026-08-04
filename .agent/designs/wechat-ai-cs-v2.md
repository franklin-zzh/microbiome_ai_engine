# 微信 AI 客服设计方案 v2（当前主线）

> 来源：2026-08-03 业务方提供《微信AI客服》设计方案（140 行评审稿）
> 状态：已定稿，作为当前 6 周计划的主线。评审意见见 `../design-review.md`。

## 一、项目目标与总体架构

### 业务目标
通过对接 微信客服（覆盖单聊、公众号、服务号、H5）与 企微群机器人（覆盖企微群），引入 Dify 智能中台 与 自建后端网关。

核心目标：
- 7×24 小时客户 FAQ 自动回答
- 自动推荐相关资料（视频、文章、产品信息）
- 将企业已有产品知识、服务流程、FAQ 转化为可查询知识库
- 收集客户满意度
- 通过客户对话信息，结合转化成功率把经验沉淀为数据
  - 生成用户画像总结，辅助销售了解用户购买意愿、关注重点，提供推荐策略
  - 总结客户重点关注的高频 Q&A

服务对象：入口 = 微信公众号、企业微信、官网聊天窗口；对象 = ToB 生态伙伴、ToC 消费者

### 总体架构

```
微信客服(单聊) ─┐
公众号/服务号   ├─> FastAPI 网关 ──> Redis(状态机/异步) ──> Celery(第3周) ──> Dify Workflow
H5 官网聊天窗  ─┘        │                                          │
企微群机器人 ────────────┘                                          ▼
                        │                                  意图分类 → FAQ KB(高阈值) → Product KB(降级)
                        ▼
                 MySQL 8 单库 mb_ai_engine：core_*(知识库体系) + cs_*(cs_chat_logs / cs_session_state / cs_leads_preview)，Alembic 迁移
```

### 流程图（消息生命周期）
```
用户消息 → 微信回调(5秒内回success) → Redis 上下文缓存 → Celery 队列
        → Dify 意图路由 → FAQ KB(高阈值) → 命中? 直接答 : 降级 Product/Procedure/Marketing KB
        → 风控(敏感词/免责声明) → 回复内容 → 主动推送微信 → 全量日志落库 cs_chat_logs
        → 置信度<0.65 → 知识库待补充清单 / 转人工 → 企微通知
```

## 二、技术范围

| 模块 | 选型 | 主要负责 |
|---|---|---|
| AI 中台 | Dify (Docker 部署) | 零代码/低代码编排 Workflow |
| API 网关 | Python (FastAPI) | 微信加解密、状态机控场、调度 Dify |
| 缓存与队列 | Redis | 5 秒异步回包、上下文临时存储、Celery 队列（本项目独占 db3） |
| 持久化存储 | **MySQL 8 单库 `mb_ai_engine`**：领域隔离靠表名前缀——`core_*`（知识库体系）+ `cs_*`（客服 Agent 专有，含 `cs_chat_logs`），表结构由 **Alembic** 迁移管理 | 对话日志湖 + 轻量线索表 |
| 向量数据库 | **Dify 内置 Weaviate**（随 Dify 自带部署，**非 pgvector**） | 不单独维护 Milvus 集群 |

> **存储选型说明（2026-08-04 修订）**：原方案“MySQL / PostgreSQL 二选一”先定 **MySQL 8 双库**，后于 2026-08-04 **合并为单库 `mb_ai_engine`**（`core_*` / `cs_*` 表名前缀隔离，双库数据经 `scripts/migrate_single_db.py` RENAME 迁移）。
> Dify 官方架构自带 `db_postgres`（仅作为 Dify 元数据库，宿主端口 5434）与 Weaviate 向量库；
> **业务数据（对话日志/会话状态/线索/知识库）100% 走 MySQL**，不承担任何 pgvector 向量职责。
> 表结构变更统一走 Alembic 迁移（`backend/migrations/`），不再使用 create_all。

### 知识库：Knowledge Base v1（四库结构）
- **Standard FAQ**：产品使用、效果、检测细节的高频 Q&A（独立 KB，高匹配阈值，置信度更高）
- **Product**：客观中立的产品与服务知识（产品定义、成分/原理、适用人群、核心优势、使用禁忌）；AI-CS 及外部客户检索
- **Procedure**：检测流程（采样指南、样本寄送流程、报告查询步骤、注意事项）
- **Marketing**：科普内容（公众号文章、视频字幕转 Markdown 切片）

### Data Pipeline
- 获取原始数据：公众号文章、视频字幕、客服聊天记录等
- ETL：采集、清洗、结构化切片、向量化、自动化同步
- 确保 KB 实效性与准确率

## 三、分阶段开发计划与时间表（6 周）

### 第 1 周：基础架构与数据库设计（当前）
- **Task 1.1（2 天）环境初始化**：docker-compose.yml 一键拉起 FastAPI；复用本地 Docker 的 MySQL 8（global-mysql8@3306）与 Redis（redis@6380/db3）；验证 Dify 私有化部署稳定性，配置 Embedding 模型与 LLM API Key。
- **Task 1.2（2 天）数据库表结构设计**（单库 `mb_ai_engine`，表名前缀隔离，Alembic 迁移）：
  - `core_*` 知识库体系：`core_knowledge_items` / `core_unanswered_questions` / `core_sales_cases`
  - `cs_*` 客服会话体系：`cs_chat_logs`（全量对话日志：OpenID、Context、RAG 召回切片、满意度、转人工标记）/ `cs_session_state`（会话状态表）/ `cs_leads_preview`（线索预备表）
- **Task 1.3（1 天）FastAPI 基础框架搭建**：初始化 FastAPI 项目结构，集成 SQLAlchemy/SQLModel，搭建 Redis 状态机管理模块。

### 第 2 周：数据 ETL 清洗与知识库录入
- **Task 2.1**：原始数据收集与结构化清洗（公众号文章、视频字幕、产品说明书 → 标准 Markdown → 语义段落切片 Semantic Chunking）
- **Task 2.2**：FAQ 问答对整理与 Q-to-Q 导入（历史高频咨询 → 标准 Q&A 对 → Excel/CSV；Dify 独立 FAQ KB 高阈值 + Product KB 混合检索+Rerank）
- **Task 2.3**：召回测试与阈值调优（Top-K 与 Rerank 权重，常见问题 100% 命中）

### 第 3 周：微信生态接入与 FastAPI 异步网关开发
- **Task 3.1**：微信客服 / 企微 API 对接（API 签名验证、消息解密与回复加密）
- **Task 3.2**：5 秒异步解耦队列（Celery + Redis 异步任务队列）
- **Task 3.3**：本地贯通测试（内网穿透，微信 → 本地 FastAPI 网关联调）

### 第 4 周：Dify 核心工作流与风控编排
- **Task 4.1**：意图路由与双轨 RAG 配置（意图分类器 → 优先查 FAQ KB → 未命中降级查 Product KB）
- **Task 4.2**：安全与风控节点（医疗免责声明强制追加；敏感词过滤 + Prompt 注入防护）
- **Task 4.3**：人工接管 HITL 逻辑（"转人工"或负面情绪 → Redis 标记 HUMAN_MODE）

### 第 5 周：全链路闭环、全量日志沉淀与线索通知
- **Task 5.1**：全量对话日志落库（Dify 返回全部元数据含上下文、检索切片得分 → 异步写 chat_logs）
- **Task 5.2**：FAQ 模板与资料入库 SOP（FAQ_导入模板.xlsx、《资料入库排版 SOP》）
- **Task 5.3**：全链路集成贯通测试（微信 → Dify → 风控 → 回复 → 日志 → 企微通知）

### 第 6 周：灰度试运行、知识库反哺与上线交付
- **Task 6.1**：攻防测试与灰度上线（Prompt 攻击测试：套底价、诱导开方、恶意诱导；小范围灰度）
- **Task 6.2**：日志监控与知识库更新（chat_logs 中低分/未命中 → 知识库待补充清单.xlsx → 补全导入）
- **Task 6.3**：文档整理与 72 小时稳定性交付（部署文档、维护 SOP、72 小时无消息丢失验收）

## 四、验收与交付标准
1. **功能标准**：微信客服/服务号/公众号单聊稳定收到 AI 回复，平均响应时长 <4 秒；企微群 @机器人 精准答疑，非 @ 消息静默；触发"转人工"后 AI 立即停止抢答，企微后台收到接管通知。
2. **稳定性标准**：连续运行 72 小时无消息丢失，微信无"服务不可用"报错；100% 走官方 API，无封号风控记录。

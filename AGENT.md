# AGENT.md - 项目认知与核心控制中心
> **AI 助手阅读指引**：这是本项目的核心上下文白皮书。每次当你进入本工作区、切换重大的子任务、或者重置上下文时，**必须首先完整阅读此文件**，以校准你的技术决策、代码风格和操作标准。

## 1. 项目基本盘 (Project Context)

- **项目名称**：`agent_cs`（AI 客服 Agent；共享知识库引擎命名为 `engine_rag`，未来可拆分为独立服务）

- **定位与目标**：面向（肠菌检测+调养）公司搭建 **微信生态 AI 客服系统（AICS）**：
    - 7×24 小时客户 FAQ 自动回答，覆盖 微信客服（单聊）、公众号/服务号、H5、企微群机器人 四大入口；
    - 自动推荐相关资料（视频、文章、产品信息）；
    - 将企业已有产品知识、服务流程、FAQ 转化为可查询知识库（`engine_rag`）；
    - 收集客户满意度；基于对话数据沉淀用户画像、高频 Q&A，辅助销售决策。

- **总体工期**：6 周（详见 §4.5 里程碑与 `.agent/designs/wechat-ai-cs-v2.md`）。

- **当前开发阶段**：**第 1 周：基础架构与数据库设计**（docker-compose 环境初始化、MySQL 单库建表、FastAPI + Redis 状态机框架）。

## 2. 技术栈与运行环境 (Tech Stack & Environment)

当你编写、重构或调试代码时，必须严格遵守以下技术规范：

- **核心语言/框架**：Python 3.11+ (FastAPI) + Pydantic v2 + SQLAlchemy 2.x
- **Agent & RAG 引擎**：Dify `1.16.1` 私有化部署于局域网服务器 `192.168.110.16`（官方 compose，nginx 统一入口：控制台与 API 均走 `http://192.168.110.16:3080`，数据卷 `/data/data2025/fmt_software/fmt-infra/dify/docker/volumes`，见 `dify/docs/OFFICIAL-DEPLOY-DIFY.md`）+ Embedding/LLM 自配 API Key
- **缓存与队列**：Redis（本地 Docker `redis` 容器，`6380` 端口，密码 `fumate`，**本项目独占 db3**；会话状态机、5 秒异步回包上下文缓存；Celery 队列于第 3 周引入）
- **数据存储**：**MySQL 8 单库 `mb_ai_engine`**（本地 Docker `global-mysql8` 容器，`3306` 端口，root/fumate）：
    - 领域隔离靠表名前缀：`core_*` 知识库体系（core_knowledge_items / core_unanswered_questions / core_sales_cases）+ `cs_*` 客服会话体系（cs_chat_logs / cs_session_state / cs_leads_preview）
    - **表结构统一由 Alembic 迁移管理**（`alembic upgrade head`），已废弃 `create_all()`；跨领域（core_* 与 cs_* 之间）不建物理外键，业务层逻辑关联，便于未来拆库
    - 向量库直接使用 Dify 内置 **Weaviate**（非 pgvector），不单独维护 Milvus
- **微信生态**：企业微信「微信客服」API（加解密 + 消息收发）、公众号/服务号被动回复与客服消息、企微群机器人 Webhook
- **环境限制**：Windows 11 + Docker Desktop (WSL2) 开发环境，部署目标为 Linux Docker 容器

> **存储选型说明（2026-08-03 定稿修订）**：业务方要求沿用 MySQL（向量库由 Dify/单独承担，而非 PostgreSQL）。
> 业务数据 100% 走 MySQL 8 单库 `mb_ai_engine`；Dify 官方架构自带的 `db_postgres` 仅作为 Dify 元数据库（仅容器内网，不映射宿主端口），不承担任何业务/向量职责。
> 表 `chat_logs` 已更名为 `cs_chat_logs`；双库（mb_ai_core / mb_ai_cs）已于 2026-08-04 合并为单库 `mb_ai_engine`（表前缀隔离，见 scripts/migrate_single_db.py）。

## 3. 架构设计与核心规则 (Architecture & Rules)

### 3.1 总体架构

```
微信客服(单聊) ─┐
公众号/服务号   ├─> FastAPI 网关 ──> Redis db3(状态机/异步缓存) ──> Celery(第3周) ──> Dify Workflow
H5 官网聊天窗  ─┘        │                                           │
企微群机器人 ────────────┘                                           ▼
                        │                                   意图分类 → FAQ KB(高阈值) → Product/Procedure/Marketing KB
                        ▼
                 MySQL 8 单库 mb_ai_engine：core_*(知识库体系) + cs_*(cs_chat_logs/cs_session_state/cs_leads_preview)，Alembic 迁移
```

### 3.2 核心规则

1. **数据源头单一与解耦原则**：
    - MySQL 为系统的 **Single Source of Truth**（单库 `mb_ai_engine`：`core_*` 知识库体系 / `cs_*` 客服会话体系，单引擎 + 单 Base，领域靠表名前缀隔离）。
    - **跨领域不建物理外键**（core_* 与 cs_* 之间），仅在同领域（core_* 内部）保留 FK 保证完整性；跨域关联在 ORM 中保留字段、在 Service 层逻辑关联，未来拆库不被外键缠住。
    - 表结构变更一律走 Alembic 迁移（migrations/），禁止在应用启动时 create_all；新增表/改字段 = 改模型 + `alembic revision --autogenerate`。
    - 知识项在关系库中控制生命周期（`DRAFT → PENDING → APPROVED → REJECTED`），严禁绕过数据库审核直接写入 Dify 向量库。
    - `cs_chat_logs` 沉淀全量对话日志（含 RAG 召回切片与得分），是后续用户画像与知识反哺的唯一数据湖。

2. **5 秒异步回包（微信硬约束）**：
    - 微信/企微回调必须在 5 秒内返回 `success`；所有 AI 生成逻辑必须异步化（Redis 缓存上下文 → Celery 队列 → Dify 调用 → 主动推送回复）。
    - 回调接口内**严禁**同步调用 Dify。

3. **安全护栏与人机协同 (HITL)**：
    - 医疗合规红线：客服 Agent 严禁确诊或给用药建议；高风险关键词（便血、剧烈腹痛等）强制转人工。
    - 会话状态机：`NORMAL / HUMAN_MODE / BLOCKED`（Redis db3 存储，TTL 管理）；用户输入"转人工"或连续负面情绪 → 置 `HUMAN_MODE`，AI 停止抢答。
    - 低置信度捕获：检索匹配分 < 0.65 时异步写入 `unanswered_questions` / 知识待补充清单。

4. **密钥与凭证安全（P0 已落地 2026-08-06）**：
    - 禁止在代码 / Dockerfile / Dify DSL 中硬编码任何 API Key、DB 密码、EncodingAESKey、Dataset Token；
    - 统一走根目录 `.env`（docker compose 插值 + backend `pydantic-settings` 读取），运行时经 `pydantic-settings` 加载；`restricted_paths` 保护 `.env` / `.pem`；
    - **默认凭据拒绝**：`DATABASE_URL / REDIS_URL / JWT_SECRET / ADMIN_USERNAME / ADMIN_PASSWORD / INTERNAL_API_KEY` 缺失即启动失败（`app/core/config.py` 必填校验），compose 一律 `${VAR:?}` 不留默认兜底；
    - **三层鉴权**：① 人类用户 → JWT（`/api/v1/auth/login`，`app/core/security.py` 的 `require_admin`）；② Dify workflow 回调/内部异步链路 → Header `X-API-Key`（`INTERNAL_API_KEY`，`require_internal_key`）；③ 微信 Webhook → 官方签名验签（WXBizMsgCrypt，POST 回调已验签+解密）；
    - **CORS 白名单配置注入**（`CORS_ORIGINS` 逗号分隔），禁止 `allow_origins=["*"]`；
    - frp 客户端真实配置（含 token）gitignore，仓库只留 `frp-client/frpc.toml.example` 模板。

5. **轻量与高扩展设计**：遵循 MVP 最小可行原则，避免过度设计（例如不引入 Milvus 集群、第一周不引入 Celery）。接口 RESTful，核心业务逻辑在 Service 层消化。

6. **结构化日志与可观测性**：核心业务节点输出结构化 JSON 日志（含 `item_id`, `domain`, `source_type`, `status`, `error_msg`），logger 名 `agent_cs`，见 `app/core/logging.py`。

## 4. 控制中心映射 (.agent/ Folder Mapping)

```
D:\projects\microbiome_ai_engine\    # 项目根目录（2026-08-03 由 gut-health-agent-platform/ 迁移至此；项目名 agent_cs）
├── AGENT.md                      # 本文件（AI 核心索引与全局大纲）
├── docker-compose.yml            # 网关栈：FastAPI backend（MySQL/Redis 复用宿主容器）
├── docker-compose.dify.yml       # Dify 私有化部署精简参考版（对外 web:3080 / api:5081；服务器实际用官方 compose，见 dify/docs/OFFICIAL-DEPLOY-DIFY.md）
├── .env.dify.example             # Dify 部署环境变量模板（复制为 .env.dify 使用）
├── DEPLOY-DIFY.md                # Dify 局域网服务器部署手册（部署到 192.168.110.16）
├── backend/                      # FastAPI 网关服务
│   ├── app/
│   │   ├── core/                 # 公共基础设施：config / database(单引擎+单Base) / redis / wxbizmsgcrypt / logging / security(JWT+鉴权依赖) / auth_router(登录)
│   │   ├── clients/              # 外部服务 Client（dify_client.py：Dify SDK 统一封装）
│   │   ├── knowledge/            # 知识库域（横切共享，mb_ai_engine.core_*）：models / schemas / services / router
│   │   ├── agent_cs/             # 客服 Agent 域（mb_ai_engine.cs_*）：models / schemas / services(状态机) / router(含微信回调)
│   │   ├── agent_sales/          # 销售 Agent 域（Phase 2 预留，mb_ai_engine.cs_leads_preview）：models / schemas / extractor / router
│   │   └── agent_doctor/         # 医生 Agent 域（Phase 3 预留）：models 占位
│   ├── scripts/                  # 初始化与迁移脚本（setup_db.py 建单库 / migrate_single_db.py 双库→单库 / import_seed.py 种子）
│   ├── migrations/               # Alembic 迁移（alembic.ini + env.py + versions/，连接串见 env.py get_url）
│   ├── tests/                    # pytest 单元测试（conftest.py 自动指向 mb_ai_engine_test + alembic upgrade head 建表）
│   └── main.py                   # FastAPI 应用入口（表结构由 Alembic 管理，启动不再 create_all）
├── dify-workflows/               # Dify 导出 Workflow DSL (YAML)
├── frp-client/                   # 内网穿透客户端（frpc.toml 含 token 不入库；模板 frpc.toml.example）
└── .agent/
    ├── config.json               # 全局模型行为、安全权限与排除目录配置
    ├── prd.md                    # 产品需求文档（旧版 MVP 底座，知识审核闭环继续复用）
    ├── designs/
    │   └── wechat-ai-cs-v2.md    # ★ 微信 AI 客服设计方案 v2（当前主线，140 行评审稿）
    ├── design-review.md          # 方案评审意见（亮点 / 修正点 / 待确认）
    ├── prompts/
    │   └── requirement_doc.txt   # 需求快照
    ├── rules/                    # 模块化代码规范库
    │   ├── api-conventions.md    # FastAPI RESTful API 规范
    │   ├── code-style.md         # Python 命名与异常处理规范
    │   ├── database-schema.md    # MySQL 8 单库表结构与索引规范（mb_ai_engine：core_* + cs_*）
    │   └── medical-safety.md     # 医疗合规与客服 Guardrails 安全规则
    └── workflows/
        └── sync-requirement.md   # 需求同步流程
```

### 4.5 业务蓝图与排期里程碑（6 周计划）

> 详细方案见 `.agent/designs/wechat-ai-cs-v2.md`；旧版"肠道 AI 智脑引擎 MVP"见 `.agent/prd.md`（其知识审核闭环能力继续复用）。

- [x] **旧版 MVP 底座（已交付）**：knowledge_items / unanswered_questions / sales_cases 三表（现归入 `mb_ai_engine.core_*`）；审核通过 Hook → Dify Sync；CS Agent Workflow DSL；种子数据与 pytest 9/9 通过。
- [ ] **[W1] 基础架构与数据库设计（当前）**
    - [x] Task 1.1 环境初始化：docker-compose 拉起 FastAPI；复用本地 Docker MySQL8（global-mysql8@3306）与 Redis（redis@6380/db3）；Dify 部署配置（docker-compose.dify.yml，镜像 1.16.1，db_postgres 宿主端口 5434 / redis 6381 避开宿主冲突）
    - [x] Task 1.2 数据库表结构：`mb_ai_engine` 单库 + `core_*`/`cs_*` 表名前缀（core_knowledge_items/core_unanswered_questions/core_sales_cases + cs_chat_logs/cs_session_state/cs_leads_preview）
    - [x] Task 1.3 FastAPI 基础框架：SQLAlchemy 单引擎（engine/SessionLocal）+ Redis 状态机管理模块
    - [x] Task 1.4 单库合并 + Alembic：双库 RENAME 迁移（scripts/migrate_single_db.py）→ 删除 create_all → `alembic init` + init_db 迁移 → 测试库 alembic upgrade head 建表（pytest 9/9）
- [ ] **[W2] 数据 ETL 清洗与知识库录入**：公众号文章/视频字幕采集清洗 → Semantic Chunking → FAQ 问答对（Q-to-Q）→ Dify FAQ KB / Product KB 检索阈值调优
- [ ] **[W3] 微信生态接入与异步网关**：企微微信客服 API 加解密路由、Celery + Redis 异步队列、frp 内网穿透本地联调
- [ ] **[W4] Dify 核心工作流与风控编排**：意图路由（FAQ 优先 → Product 降级）、医疗免责声明、敏感词拦截、Prompt 注入防护、HITL 转人工分支
- [ ] **[W5] 全链路闭环**：对话日志 100% 落库、FAQ 导入模板与资料入库 SOP、全链路集成测试
- [ ] **[W6] 灰度试运行与交付**：攻防测试、灰度、知识库待补充清单反哺、72 小时稳定性验收

## 5. 常用开发原子操作 (Core Workflows)

### 🛠️ 后端启动与检查 (Backend Run & Lint)

- **启动开发服务（一键，自动用 .venv）**：`backend\dev.bat`（双击 / cmd）或 `backend\dev.ps1`（PowerShell）；VSCode 里 Ctrl+Shift+B 选「启动后端 (uvicorn --reload)」。等价手动命令：`cd backend && .venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000`
- **代码质量检查**：`flake8 backend/` 或 `ruff check backend/`
- **预期结果**：服务正常在 `http://localhost:8000` 启动，Swagger 文档展示在 `/docs`，`/health` 返回 `{"status":"ok","redis":"ok"}`。

### 🐳 容器化本地编排 (Docker Local Environment)

- **一键启动网关栈（仅 backend，MySQL/Redis 复用宿主容器）**：`docker compose up -d`
- **初始化 MySQL 单库**：`cd backend && .venv\Scripts\python.exe scripts\setup_db.py`（建 mb_ai_engine / mb_ai_engine_test）
- **双库 → 单库数据迁移（仅旧环境执行一次）**：`cd backend && .venv\Scripts\python.exe scripts\migrate_single_db.py`（RENAME TABLE 跨库搬表 + 索引改名），随后 `alembic stamp head`
- **应用表结构迁移**：`cd backend && $env:DATABASE_URL="mysql+pymysql://root:fumate@localhost:3306/mb_ai_engine?charset=utf8mb4"; .venv\Scripts\python.exe -m alembic upgrade head`（docker compose 启动时自动执行）
- **启动 Dify 私有化（服务器 192.168.110.16）**：服务器实际运行官方 compose（`/data/data2025/fmt_software/fmt-infra/dify/docker`，nginx 统一入口 `http://192.168.110.16:3080`），完整步骤见 `dify/docs/OFFICIAL-DEPLOY-DIFY.md`；仓库内 `docker-compose.dify.yml` 为精简参考版（web:3080 / api:5081），仅本地参考/回滚用
- **查看服务日志**：`docker compose logs -f backend`

### 🧪 自动化测试 (Testing)

- **测试命令**：`cd backend && .venv\Scripts\python.exe -m pytest tests/ -v`（conftest.py 自动指向 mb_ai_engine_test 并执行 alembic upgrade head 建表，无需手工设置 TEST_DATABASE_URL）
- **提交流程**：提交 Git 或更新逻辑前，**必须**确保 `tests/` 下全部用例通过（含 knowledge 闭环 + chat/session 状态机）。

## 6. 动态记忆区 (Session Memory)

> 🚨 **AI 助手硬性红线指令 (CRITICAL AI EXECUTABLE RULE)**：
> 1. 每次当你【成功修复重大 Bug】、【完成关键代码重构】或【交付阶段性接口】后，**必须立刻调用文件改写工具**更新本小节。
> 2. 严禁偷懒合并时间！【最近一次同步时间】必须精确到分钟，格式严格锁定为：`YYYY-MM-DD HH:mm`。
> 3. 每次更新时，必须同步清理已完成的 Todo，并将下一步最硬核的技术焦点写在【当前关注的架构焦点】中。

- **最近一次同步时间**：2026-08-12 17:35

- **当前关注的架构焦点**：知识库双形态落地 + 旧链路下线（2026-08-12）——① 旧知识项链路（`core_knowledge_items` 手工录入 + `core_sync_tasks` 对账 outbox + 旧 `dify_client.py`）整体下线：路由/模型/迁移/seed/admin 清理，unanswered/sales-case 保留但不再关联知识项（迁移 `c9d8e7f6a5b4`）；② DOC 知识库（非 QA 文件）：`core_knowledge_documents.doc_form`（qa_model/text_model/hierarchical_model）单选决定目标库，text_model 走 `dify_knowledge_client.create_by_file` 直传（Dify 原生切割，CS_DOC_DATASET_ID=60c9bb5b-...），qa_model 走 Knowledge Pipeline；一个文档一个形态一个库，未配置拒绝发布；③ 客服工作流双库检索：`cs_agent_chatbot.yml` 改为 CS_QA（semantic 0.65）+ CS_DOC（hybrid 0.3）→ merge（code 节点带 source）→ Rerank（bge-reranker-v2-m3，top_n 5）→ LLM；④ 客户端合并为 `app/clients/dify_knowledge_client.py`（直传 + Pipeline + 轮询/segments/删除）。pytest 31/31。下一步：Dify 控制台导入新工作流并配置 rerank 模型；跑 `verify_dify_contract.py` 契约实测。

- **待办遗留事项 (Todo)**：
    - [x] 1. 方案评审：`cs_chat_logs` / `session_state` / `leads_preview` 三表 DDL 设计（含索引、channel 维度），后合并为单库 `mb_ai_engine`（core_*/cs_* 前缀）。
    - [x] 2. Redis 连接模块 `app/core/redis.py` + 会话状态机 `app/agent_cs/services.py`（db3，由 app/services/session_state.py 迁入）。
    - [x] 3. 微信回调 POST 占位路由（先回 `success` 满足 5 秒约束，异步链路第 3 周接入）。
    - [x] 4. docker-compose.yml 复用宿主 MySQL8/Redis；docker-compose.dify.yml（官方镜像 1.16.1，端口 5434/6381）。
    - [x] 5. P0 安全基线：JWT+角色鉴权（require_admin/require_internal_key）、/auth/login、CORS 白名单注入、默认凭据必填（config 校验 + compose `:?`）、微信 POST 验签+解密、frp token 轮换移出 git。pytest 22/22。
    - [ ] 6. ⚠️ 服务器端 frps.toml auth.token 同步为新 token（本地 frpc.toml 已生成，需与服务端一致后重启 frpc/frps）。
    - [ ] 7. 第 2 周：ETL 清洗脚本 + FAQ 问答对模板（FAQ_导入模板.xlsx 规范）。
    - [ ] 8. 第 3 周：企微微信客服消息解密后落库 + 主动推送、Celery 异步队列（回调验签/解密已提前完成）。
    - [ ] 9. 接通负面情绪计数：`increment_negative_streak` 当前是死代码，"连续负面≥2 转人工"规则未接线（AGENT.md §3.2-3 有定义，P1#7）。
    - [x] 10. 知识驳回/删除同步删 Dify 文档 + 对账脚本（P1#6，R2）——2026-08-07 落地：reject/revoke 端点 + core_sync_tasks 对账表 + Dify DELETE（1.16.1 契约见 docs/DIFY-API-CONTRACT.md）；2026-08-12 随旧链路整体下线（迁移 c9d8e7f6a5b4 删表）。
    - [ ] 11. ⏳ Dify 契约实测：`.env` 回填 DIFY_API_KEY/CS_QA_DATASET_ID 后运行 `backend/scripts/verify_dify_contract.py`（qa_model/text_model 创建-索引-删除全链路 + 路径探测），结果回填 docs/DIFY-API-CONTRACT.md 待实测清单。
    - [x] 12. 双库拆分（目标态）：2026-08-12 落地——CS_DOC 库已建并配置（60c9bb5b-...），text_model 走 create_by_file 直传；工作流双检索 + Rerank 已设计（`cs_agent_chatbot.yml`，待 Dify 导入）。
    - [x] 13. 旧知识项链路下线（2026-08-12）：`core_knowledge_items`/`core_sync_tasks` 删表，手工知识项路由移除，CS 知识一律走「原文件 → 文档/版本 → Dify」；销售/doctor 域未来走「聊天记录直接导入」工作流形态（未开发）。

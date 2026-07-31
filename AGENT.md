# AGENT.md - 项目认知与核心控制中心
> **AI 助手阅读指引**：这是本项目的核心上下文白皮书。每次当你进入本工作区、切换重大的子任务、或者重置上下文时，**必须首先完整阅读此文件**，以校准你的技术决策、代码风格和操作标准。

## 1. 项目基本盘 (Project Context)

- **项目名称**：`gut-health-agent-platform` / `肠道健康 Agent 平台`
    
- **定位与目标**：基于 MVP 理念，为（肠菌检测+调养）公司搭建一套“1个统一知识底座 + 3类角色 Agent（客服、销售 Copilot、医师 CDSS）”的轻量级 AI 矩阵平台。打通数据自进化闭环（数据捕获 $\rightarrow$ 审核入库 $\rightarrow$ 向量同步 $\rightarrow$ 智商升级），解耦底层大模型绑定风险。
    
- **当前开发阶段**：Step 1 MVP 极速落地阶段（核心数据底座搭建、FastAPI 后端、Dify 向量库同步与审核闭环）。
    

## 2. 技术栈与运行环境 (Tech Stack & Environment)

当你编写、重构或调试代码时，必须严格遵守以下技术规范：

- **最新业务确认 (2026-07-29)**：
    
    - **核心语言/框架**：Python 3.11+ (FastAPI) 轻量级服务 + Pydantic v2
        
    - **Agent & RAG 引擎**：Dify (私有化 Docker 部署 / SaaS API) + 混合模型（DeepSeek-V3/R1、Qwen-Max/Turbo）
        
    - **数据存储**：PostgreSQL 15+（核心业务与状态控制） + Dify Vector DB (PGVector / Qdrant)
        
    - **前端架构**：Vue 3 + Tailwind CSS（Admin 审核后台） / H5 (企业微信侧边栏 & 客服 Chat 挂载)
        
    - **环境限制**：Windows 11 + Docker Desktop (WSL2) 开发环境，部署目标为 Linux Docker 容器
        
- **核心依据**：所有业务细节严格对齐 `.agent/prd.md` 与 `.agent/prompts/requirement_doc.txt`。
    

## 3. 架构设计与核心规则 (Architecture & Rules)

为了保持代码库的整洁和高维护性，所有自适应修改必须符合以下规则：

1. **数据源头单一与解耦原则**：
    
    - PostgreSQL 为系统的 **Single Source of Truth（单一事实来源）**。
        
    - 所有知识项、未解答问题、销售案例均在关系型数据库中控制生命周期（`DRAFT` $\rightarrow$ `PENDING` $\rightarrow$ `APPROVED` $\rightarrow$ `REJECTED`）。严禁不经过数据库审核直接写入 Dify 向量库。
        
2. **安全护栏与人机协同 (Human-in-the-Loop)**：
    
    - **医疗合规红线**：客服 Agent 严禁给出确诊或用药诊断，遭遇高风险关键词（如便血、剧烈腹痛）强行触发安全护栏并转接企微销售。
        
    - **低置信度捕获**：客服检索匹配得分 $< 0.65$ 时，自动异步写入 `unanswered_questions` 表。
        
3. **密钥与凭证安全**：
    
    - 绝对不允许在代码或 Dify DSL 中硬编码任何 API Key、Database Password、Dify Dataset Tokens。
        
    - 必须通过 `.env` 环境变量统一管理并在运行时加载。
        
4. **轻量与高扩展设计**：
    
    - 遵循 MVP 最小可行性原则，避免过度设计。接口设计遵循 RESTful 规范，核心业务逻辑在 Service 层消化，异步同步任务使用 FastAPI `BackgroundTasks` 执行。
        
5. **结构化日志与可观测性**：
    
    - 核心业务节点（如：未解答问题捕获、知识审核 Hook、Dify API 向量同步失败）必须输出结构化 JSON 日志（包含 `item_id`, `domain`, `source_type`, `status`, `error_msg`）。
        

## 4. 控制中心映射 (.agent/ Folder Mapping)

本项目在隐藏目录 `.agent/` 下设立了模块化的指令中心，请按需加载：

Plaintext

```
microbiome-ai-engine/             # 项目根目录
├── AGENT.md                      # 本文件（AI 核心索引与全局大纲）
├── .env.example                  # 环境变量配置模版
├── backend/                      # FastAPI 服务、Core DB 交互、Dify 同步引擎
│   ├── app/
│   │   ├── api/                  # REST API 路由 (cs, sales, admin, knowledge)
│   │   ├── models/               # PostgreSQL ORM 实体模型
│   │   ├── services/             # 业务逻辑与 Dify Sync 引擎
│   │   └── core/                 # 数据库连接与安全配置
│   └── main.py                   # FastAPI 应用入口
├── admin-web/                    # 极简 Web 审核后台 (Vue 3 / React)
├── sales-copilot-sidebar/        # 企微侧边栏 H5 前端
├── dify-workflows/               # Dify 导出的 Workflow/Agent DSL (YAML) 配置文件
└── .agent/
    ├── config.json               # 全局模型行为、安全权限与排除目录配置
    ├── prd.md                    # 肠道微生态 AI 智脑引擎 PRD 需求文档
    ├── prompts/
    │   └── requirement_doc.txt   # 最新 MVP 需求与 API 契约快照
    ├── rules/                    # 模块化代码规范库
    │   ├── api-conventions.md    # FastAPI RESTful API 规范
    │   ├── code-style.md         # Python / Vue3 命名与异常处理规范
    │   ├── database-schema.md    # PostgreSQL 3张核心表设计与索引规范
    │   └── medical-safety.md     # 医疗合规与客服 Guardrails 安全规则
    └── commands/
        ├── review.md             # 代码自审与 CR 标准
        └── deploy.md             # Docker Compose 本地编排与部署脚本
```

### 4.5 业务蓝图与排期里程碑

- **详细产品需求文档 (PRD)**：详见 `.agent/prd.md`
    
- **当前核心里程碑 (7-9 天 MVP)**：
    
    - [x] **[Day 1-2] 基建准备**：PostgreSQL 数据库搭建，初始化 `knowledge_items`、`unanswered_questions`、`sales_cases` 三张核心表；Dify 创建 `CS_KB` 与 `Sales_KB` 数据集。
        
    - [x] **[Day 3-5] 后端开发 & 客服闭环**：FastAPI 审核通过 Hook 与 Dify Sync API 联调；搭建 CS Agent Workflow 低分回调捕获逻辑。
        
    - [ ] **[Day 6-7] 销售 Copilot & Admin 后台**：开发 Vue3 审核后台（一键审核/同步）；完成企微侧边栏“话术推荐”与“案例一键提炼”提交功能。
        
    - [ ] **[Day 8-9] 闭环联调与 Demo**：跑通“问答捕获 $\rightarrow$ 审核同步 $\rightarrow$ 命中验证 $\rightarrow$ 销售案例沉淀”完整链路演示。
        

## 5. 常用开发原子操作 (Core Workflows)

在执行日常开发与测试时，请运行以下工作流指令：

### 🛠️ 后端启动与检查 (Backend Run & Lint)

- **启动开发服务**：`uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000`
    
- **代码质量检查**：`flake8 backend/` 或 `ruff check backend/`
    
- **预期结果**：服务正常在 `http://localhost:8000` 启动，Swagger 文档展示在 `/docs`，零致命 Warning。
    

### 🐳 容器化本地编排 (Docker Local Environment)

- **一键启动 PostgreSQL与服务**：`docker compose up -d`
    
- **查看服务日志**：`docker compose logs -f backend`
    

### 🧪 自动化测试 (Testing)

- **测试命令**：`pytest tests/`
    
- **提交流程**：在提交 Git 或更新逻辑前，**必须**确保 `tests/test_dify_sync.py` 与 `tests/test_knowledge_api.py` 全部通过。
    

## 6. 动态记忆区 (Session Memory)

> 🚨 **AI 助手硬性红线指令 (CRITICAL AI EXECUTABLE RULE)**：
> 
> 1. 每次当你【成功修复重大 Bug】、【完成关键代码重构】或【交付阶段性接口】后，**必须立刻调用文件改写工具**更新本小节。
>     
> 2. 严禁偷懒合并时间！【最近一次同步时间】必须精确到分钟，格式严格锁定为：`YYYY-MM-DD HH:mm`（如 `2026-07-29 10:15`）。
>     
> 3. 每次更新时，必须同步清理已完成的 Todo，并将下一步最硬核的技术焦点写在【当前关注的架构焦点】中。
>     

- **最近一次同步时间**：2026-07-30 16:37
    
- **当前关注的架构焦点**：PostgreSQL 本地容器（端口 5433）已成功对接，`gut_health` 与 `gut_health_test` 数据库已建好并导入 10 条 CS FAQ 和 5 条 Sales Case 种子数据；Backend API 接口及 Dify Sync Mock 全部通过 9/9 自动化单元测试。下一步重点聚焦前端（Admin 审核后台与企微侧边栏 H5）开发或 Dify Workflow 线上联调。
    
- **待办遗留事项 (Todo)**：
    
    - [x] 1. 在 PostgreSQL 中创建 `knowledge_items`、`unanswered_questions` 与 `sales_cases` 数据表及索引（DDL 已落库到 `.agent/rules/database-schema.md`）。
        
    - [x] 2. 编写 `POST /api/v1/admin/knowledge/{item_id}/approve` 接口，结合 `BackgroundTasks` 实现调用 Dify Dataset API 写入向量文档。
        
    - [x] 3. 编写 `POST /api/v1/cs/unanswered/capture` 回调接口，打通客服置信度得分 $< 0.65$ 时的自动缺口捕获逻辑。
        
    - [x] 4. 编写 `POST /api/v1/knowledge/submit` 接口，支持销售侧边栏提交结构化提炼后的案例（`source_type = 'SALES_CASE'`）。
        
    - [x] 5. 创建 Dify Workflow DSL（客服 Agent、销售 Copilot、案例提炼器）并导入说明。
        
    - [x] 6. 编写种子数据与 `pytest` 测试用例，完成本地联调。
        
    - [ ] 7. 开发 Admin 审核 Web 后台 (Vue 3 / React) 与企微侧边栏 H5 前端。
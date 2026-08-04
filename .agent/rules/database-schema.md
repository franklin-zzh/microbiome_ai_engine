# 数据库表结构设计 (.agent/rules/database-schema.md)

> 适用范围：agent_cs 全阶段。
> 原则：**MySQL 8 单库 `mb_ai_engine`**（本地 Docker 部署 `global-mysql8`@3306, root/fumate），
> 领域隔离靠**表名前缀**：`core_*` 知识库体系 + `cs_*` 客服会话体系（含销售线索）。
> 知识项、未解答问题、销售案例在 MySQL 控制生命周期，再经 Sync Engine 异步同步到 Dify 向量库（Weaviate）。
>
> ⚠️ **DDL 唯一权威 = Alembic 迁移**（`backend/migrations/`，`alembic upgrade head` 执行）。
> 本文档描述**逻辑结构**（便于评审与沟通），字段/索引细节一律以
> `backend/app/**/models.py` + `backend/migrations/versions/` 为准；
> 禁止手工 DDL、禁止应用启动时 `create_all()`。

## 0. 库与命名约定

```sql
CREATE DATABASE IF NOT EXISTS `mb_ai_engine`      CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS `mb_ai_engine_test` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;  -- pytest 使用
-- 历史双库 mb_ai_core / mb_ai_cs 已合并（scripts/migrate_single_db.py），迁移后保留为空库，可手工 DROP
```

- 表名小写、下划线分隔；**所有表名带领域前缀**：知识库体系 `core_*`（`core_knowledge_items` / `core_unanswered_questions` / `core_sales_cases`），客服会话体系 `cs_*`（`cs_chat_logs` / `cs_session_state` / `cs_leads_preview`）。
- **跨领域（core_* ↔ cs_*）不建物理外键**：仅在同领域（core_* 内部）保留 FK 保证完整性；跨域关联在 ORM 保留字段、Service 层逻辑关联，避免未来拆库被外键缠住。
- 枚举列使用 MySQL `ENUM`（列内联，无独立 TYPE）；JSON 使用 MySQL `JSON`；时间列使用 `DATETIME`（`DEFAULT CURRENT_TIMESTAMP`）。
- 主键自增；`index=True` 的列由 SQLAlchemy 自动命名索引 `ix_<表名>_<列>`。

## 1. 核心实体关系（core_* 领域）

```
core_knowledge_items  ──<  core_unanswered_questions  (cs_gap_id 可选外键)
   │
   │  domain='SALES' 时可选关联
   └──>  core_sales_cases  (sales_case_id 可选外键)
```

- 客服缺口答案与销售案例最终都会沉淀为 `core_knowledge_items` 中的一条知识。
- `core_sales_cases` 单独保存原始聊天记录与 AI 提炼结果，便于运营二次校验。
- 三表存在**循环外键**（core_knowledge_items ↔ core_unanswered_questions / core_sales_cases），均在 core 领域内部；
  迁移脚本中这两个 FK 在建表后追加（MySQL 不允许建表时引用未创建的表）。

## 2. DDL（逻辑结构；实际以 Alembic 迁移为准）

### 2.1 core_knowledge_items（统一知识库 / 话术库 · core_* 领域）

```sql
CREATE TABLE core_knowledge_items (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    domain          ENUM('CS','SALES','DOCTOR') NOT NULL DEFAULT 'CS',
    source_type     ENUM('MANUAL','CS_GAP','SALES_CASE') NOT NULL DEFAULT 'MANUAL',
    status          ENUM('DRAFT','PENDING','APPROVED','REJECTED') NOT NULL DEFAULT 'PENDING',
    category        VARCHAR(64) NOT NULL DEFAULT 'GENERAL',  -- 主分类（强规范枚举/路径，如 product.probiotics）；与 tags 职责分离，支持索引筛选

    title           VARCHAR(255) NOT NULL,
    question        TEXT,                       -- 客服 FAQ 的“问题”
    answer          TEXT NOT NULL,              -- 标准答案 / 销售话术正文
    tags            JSON,                       -- 标签数组

    -- 领域内可选外键（core 内部）：CS_GAP / SALES_CASE 来源
    cs_gap_id       BIGINT REFERENCES core_unanswered_questions(id) ON DELETE SET NULL,
    sales_case_id   BIGINT REFERENCES core_sales_cases(id) ON DELETE SET NULL,

    -- Dify 侧文档 ID，审核同步成功后回填
    vector_doc_id   VARCHAR(255),

    created_by      VARCHAR(128),
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    approved_at     DATETIME,
    approved_by     VARCHAR(128)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 索引以 models.py 为准（index=True 自动命名 ix_<表名>_<列>），如 ix_core_knowledge_items_id / ix_core_knowledge_items_category（category 等值筛选）
```

### 2.2 core_unanswered_questions（客服未解答问题捕获 · core_* 领域）

```sql
CREATE TABLE core_unanswered_questions (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_query      TEXT NOT NULL,              -- 用户原始问题
    normalized_query TEXT,                      -- 归一化/去重后的问题
    context         JSON,                       -- 可选上下文：session_id, user_id, channel
    match_score     VARCHAR(10),                -- Dify 返回的最佳匹配分（文本保留，避免精度问题）
    status          ENUM('OPEN','PROCESSING','RESOLVED','IGNORED') NOT NULL DEFAULT 'OPEN',

    knowledge_item_id BIGINT REFERENCES core_knowledge_items(id) ON DELETE SET NULL,  -- 关联最终知识项

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    resolved_at     DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 2.3 core_sales_cases（销售实战案例 · core_* 领域）

```sql
CREATE TABLE core_sales_cases (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    submitted_by        VARCHAR(128) NOT NULL,  -- 销售企微 UserId
    raw_chat_log        TEXT NOT NULL,          -- 原始聊天记录

    customer_type       VARCHAR(255),
    core_objection      TEXT,
    breakthrough_logic  TEXT,
    follow_up_script    TEXT,
    extracted_summary   JSON,

    status              ENUM('PENDING','APPROVED','REJECTED') NOT NULL DEFAULT 'PENDING',
    knowledge_item_id   BIGINT REFERENCES core_knowledge_items(id) ON DELETE SET NULL,

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    approved_at         DATETIME,
    approved_by         VARCHAR(128)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

## 3. 状态机

### core_knowledge_items
```
DRAFT --(提交审核)--> PENDING --(approve)--> APPROVED --(sync OK)--> vector_doc_id 回填
                       |
                       └--(reject)--> REJECTED
```

### core_unanswered_questions
```
OPEN --(运营补答案并审核)--> RESOLVED (knowledge_item_id 回填)
   |
   └--(判定为噪声/重复)--> IGNORED
```

### core_sales_cases
```
PENDING --(approve)--> APPROVED --(sync OK)--> knowledge_item_id 回填
   |
   └--(reject)--> REJECTED
```

## 4. 与 Dify 向量库的对应关系（engine_rag）

| mb_ai_engine 表 | Dify Dataset | 说明 |
|---|---|---|
| `core_knowledge_items` where `domain='CS'` | `CS_KB` | 客服 FAQ 与标准答案 |
| `core_knowledge_items` where `domain='SALES'` | `Sales_KB` | 销售话术与成交案例 |
| `core_knowledge_items` where `domain='DOCTOR'` | 可复用 `CS_KB` 或单独 `Medical_KB` | 医疗专业内容，需医师审核 |

同步原则：
1. 只同步 `status='APPROVED'` 的项。
2. 同步前检查 `vector_doc_id`：为空则创建文档，非空则更新文档。
3. 同步失败必须记录结构化日志并保留重试可能（status 保持 APPROVED，通过失败日志告警）。

---

# v2 微信 AI 客服：对话日志湖与会话控制表（2026-08-04 修订为单库 + cs_* 前缀）

> 适用范围：微信 AI 客服平台 v2（`.agent/designs/wechat-ai-cs-v2.md`），表归属 `mb_ai_engine`（`cs_*` 前缀）。
> 原则：`cs_chat_logs` 是全量对话日志湖（Data Lake），任何一条用户消息与 AI 回复**必须**落库；
> `cs_session_state` 是 Redis 会话状态机的持久化镜像（运行时以 Redis 为准，TTL 过期后从本表重建）；
> `cs_leads_preview` 为线索预备表，由低置信度/高意向对话异步生成，供销售跟进。
> `cs_*` 表之间及与 `core_*` 之间均**无物理外键**（跨域解耦原则）。

## 5. channel 枚举（四类微信入口）

```sql
-- MySQL 枚举值（列内联 ENUM，无需独立 CREATE TYPE）
-- 'WXKF'        = 企微「微信客服」单聊
-- 'MP'          = 公众号 / 服务号
-- 'H5'          = 官网聊天窗口（自建 H5）
-- 'WECOM_GROUP' = 企微群机器人（按群维度建会话，非 @ 消息静默）
```

## 6. 三张 cs_* 表 DDL（逻辑结构）

### 6.1 cs_chat_logs（全量对话日志湖）

```sql
CREATE TABLE cs_chat_logs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    channel         ENUM('WXKF','MP','H5','WECOM_GROUP') NOT NULL,  -- 入口渠道
    session_id      VARCHAR(128) NOT NULL,          -- 会话 ID（Redis 侧同步使用）
    open_id         VARCHAR(128) NOT NULL,          -- 用户 OpenID（企微 external_userid / 公众号 openid）

    user_message    TEXT NOT NULL,                  -- 用户原始消息
    ai_reply        TEXT,                           -- AI 回复内容
    retrieved_chunks JSON,                          -- Dify RAG 召回切片（含文本与得分）
    intent          VARCHAR(64),                    -- 意图分类结果（FAQ/PRODUCT/PROCEDURE/MARKETING/UNKNOWN）

    match_score     DECIMAL(5,4),                   -- 最佳召回得分（<0.65 触发缺口捕获）
    satisfaction    SMALLINT,                       -- 满意度（1-5，5.2 节验收；NULL=未收集）
    hit_human       BOOLEAN NOT NULL DEFAULT FALSE, -- 是否转人工
    human_takeover  VARCHAR(128),                   -- 接管人（企微 UserId）

    risk_flag       BOOLEAN NOT NULL DEFAULT FALSE, -- 是否命中医疗高风险关键词
    meta            JSON,                           -- 扩展元数据（模型、耗时、消息ID等）

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
-- 索引：ix_cs_chat_logs_created_at / ix_cs_chat_logs_id / ix_cs_chat_logs_open_id / ix_cs_chat_logs_session_id
-- 高并发写入场景建议按时间分区（后续按月 range partition），MVP 阶段先不分区
```

### 6.2 cs_session_state（会话状态表）

```sql
CREATE TABLE cs_session_state (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL UNIQUE,   -- 与 cs_chat_logs.session_id 一致
    channel         ENUM('WXKF','MP','H5','WECOM_GROUP') NOT NULL,
    open_id         VARCHAR(128) NOT NULL,

    state           ENUM('NORMAL','HUMAN_MODE','BLOCKED') NOT NULL DEFAULT 'NORMAL',
    context         JSON,                           -- 上下文快照（最近 N 轮摘要等）
    negative_streak SMALLINT NOT NULL DEFAULT 0,    -- 连续负面情绪计数（>=2 转 HUMAN_MODE）

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    expires_at      DATETIME                        -- 会话不活跃过期时间点：每次 route 滚动刷新（now + session_ttl_seconds），与 Redis TTL 对齐
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
-- 索引：ix_cs_session_state_id / ix_cs_session_state_open_id / ix_cs_session_state_session_id(unique)
```

### 6.3 cs_leads_preview（线索预备表）

```sql
CREATE TABLE cs_leads_preview (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL,
    open_id         VARCHAR(128) NOT NULL,
    channel         ENUM('WXKF','MP','H5','WECOM_GROUP') NOT NULL,

    intent_tags     JSON,                           -- 画像标签（购买意愿/关注重点/品类偏好）
    summary         TEXT,                           -- AI 生成的用户画像总结
    confidence      DECIMAL(5,4),                   -- 线索置信度
    status          ENUM('NEW','ASSIGNED','CONVERTED','CLOSED') NOT NULL DEFAULT 'NEW',
    assigned_to     VARCHAR(128),                   -- 跟进销售（企微 UserId）

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
-- 索引：ix_cs_leads_preview_created_at / ix_cs_leads_preview_id / ix_cs_leads_preview_open_id
```

## 7. 会话状态机（Redis + DB 双写）

```
                    ┌─ 连续负面情绪(>=2) / 输入"转人工" ─┐
NORMAL ────────────► HUMAN_MODE ◄──────────────────────┐
   │                                                     │ 人工完成接管
   └─ 命中高风险医疗关键词 ──► BLOCKED ──────────────────┘ (管理员/超时恢复 NORMAL)
```

- 运行时状态存 Redis：`session:{session_id}:state`，TTL 默认 30 分钟，`session:{session_id}:neg_streak` 计数。
- Redis 连接：`redis://:fumate@localhost:6380/3`（本地 Docker `redis` 容器，**本项目独占 db3**，db0-2 留给其他项目/Dify 调试）。
- `cs_session_state` 表为持久化镜像：`state` 变化时同步写表，`expires_at` 每次 route 滚动刷新（now + TTL）。
- **DB 兜底恢复**：Redis 状态缺失（TTL 过期/重启）时，下次 route 前从 `cs_session_state` 恢复 `HUMAN_MODE/BLOCKED` 并续 TTL（`router._restore_state_from_db`）——防止用户沉默超 TTL 后回来，转人工/拦截状态丢失导致 AI 重新抢答；`GET /session` 在 Redis 缺失时展示 DB 最后状态。
- **清理策略**：行在"不活跃过期后再保留 `session_retention_days`（默认 30 天）"才删除。双通道：① 惰性清理——每次 route 顺带 `DELETE ... WHERE COALESCE(expires_at, updated_at) < now - 保留期 LIMIT 200`（`router._lazy_cleanup_expired`）；② `scripts/cleanup_sessions.py`（支持 `--dry-run`，W3 Celery 落地后可挂定时）。历史 NULL `expires_at` 行用 `updated_at` 兜底。
- `HUMAN_MODE` 下 AI 停止抢答；`BLOCKED` 下仅输出固定安全话术并转人工。

## 8. 表结构迭代纪律（Alembic）

- **禁止** `create_all()` 与手工 DDL；唯一入口是 Alembic 迁移。
- 改表流程（改模型 → 生成迁移 → 审阅 → 执行）：
  ```powershell
  cd backend
  $env:DATABASE_URL="mysql+pymysql://root:fumate@localhost:3306/mb_ai_engine?charset=utf8mb4"
  .\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "描述"
  # 人工审阅 migrations/versions/ 新文件（autogenerate 需确认）
  .\.venv\Scripts\python.exe -m alembic upgrade head
  ```
- 新表/新列/新索引必须带领域前缀表名；**跨领域禁止新增物理外键**。
- 测试库 `mb_ai_engine_test` 由 pytest 会话自动 `alembic upgrade head` 重建（见 tests/conftest.py），无需手工干预。
- 已有数据的库升级：先 `alembic upgrade head`（增量迁移），不要手动 DROP 重建。

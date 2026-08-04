# 数据库表结构设计 (.agent/rules/database-schema.md)

> 适用范围：agent_cs MVP 阶段。
> 原则：MySQL 8 双库（本地 Docker 部署 `global-mysql8`@3306, root/fumate）：
>   - `mb_ai_core`：公共用户 / 知识库索引（knowledge_items / unanswered_questions / sales_cases）
>   - `mb_ai_cs`：客服 Agent 专有库（cs_chat_logs / session_state / leads_preview）
> 知识项、未解答问题、销售案例在 MySQL 控制生命周期，再经 Sync Engine 异步同步到 Dify 向量库（Weaviate）。

## 0. 库与命名约定

```sql
CREATE DATABASE IF NOT EXISTS `mb_ai_core` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS `mb_ai_cs`   CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- 测试库：mb_ai_core_test / mb_ai_cs_test（pytest 隔离使用）
```

- 表名小写、下划线分隔；客服会话相关表统一 `cs_` 前缀（如 `cs_chat_logs`）。
- 枚举列使用 MySQL `ENUM`；JSON 使用 MySQL `JSON` 类型；时间列使用 `DATETIME`（`DEFAULT CURRENT_TIMESTAMP`）。
- 主键自增 `BIGINT AUTO_INCREMENT`。

## 1. 核心实体关系（mb_ai_core）

```
knowledge_items  ──<  unanswered_questions  (cs_gap_id 可选外键)
   │
   │  domain='SALES' 时可选关联
   └──>  sales_cases  (sales_case_id 可选外键)
```

- 客服缺口答案与销售案例最终都会沉淀为 `knowledge_items` 中的一条知识。
- `sales_cases` 单独保存原始聊天记录与 AI 提炼结果，便于运营二次校验。

## 2. DDL

### 2.1 knowledge_items（统一知识库 / 话术库 · mb_ai_core）

```sql
CREATE TABLE knowledge_items (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    domain          ENUM('CS','SALES','DOCTOR') NOT NULL DEFAULT 'CS',
    source_type     ENUM('MANUAL','CS_GAP','SALES_CASE') NOT NULL DEFAULT 'MANUAL',
    status          ENUM('DRAFT','PENDING','APPROVED','REJECTED') NOT NULL DEFAULT 'PENDING',

    title           VARCHAR(255) NOT NULL,
    question        TEXT,                       -- 客服 FAQ 的“问题”
    answer          TEXT NOT NULL,              -- 标准答案 / 销售话术正文
    tags            JSON,                       -- 标签数组

    -- 外键：当 source_type='CS_GAP' 时记录来源缺口
    cs_gap_id       BIGINT REFERENCES unanswered_questions(id) ON DELETE SET NULL,
    -- 外键：当 source_type='SALES_CASE' 时记录来源案例
    sales_case_id   BIGINT REFERENCES sales_cases(id) ON DELETE SET NULL,

    -- Dify 侧文档 ID，审核同步成功后回填
    vector_doc_id   VARCHAR(255),

    created_by      VARCHAR(128),               -- 运营账号 / 销售企微 UserId
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    approved_at     DATETIME,
    approved_by     VARCHAR(128)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_knowledge_items_domain_status ON knowledge_items(domain, status);
CREATE INDEX idx_knowledge_items_source_type ON knowledge_items(source_type);
CREATE INDEX idx_knowledge_items_vector_doc_id ON knowledge_items(vector_doc_id);
```

### 2.2 unanswered_questions（客服未解答问题捕获 · mb_ai_core）

```sql
CREATE TABLE unanswered_questions (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_query      TEXT NOT NULL,              -- 用户原始问题
    normalized_query TEXT,                      -- 归一化/去重后的问题
    context         JSON,                       -- 可选上下文：session_id, user_id, channel
    match_score     DECIMAL(5,4),               -- Dify 返回的最佳匹配分
    status          ENUM('OPEN','PROCESSING','RESOLVED','IGNORED') NOT NULL DEFAULT 'OPEN',

    -- 关联最终生成的知识项
    knowledge_item_id BIGINT REFERENCES knowledge_items(id) ON DELETE SET NULL,

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    resolved_at     DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_unanswered_questions_status ON unanswered_questions(status);
CREATE INDEX idx_unanswered_questions_created_at ON unanswered_questions(created_at);
CREATE INDEX idx_unanswered_questions_knowledge_item_id ON unanswered_questions(knowledge_item_id);
```

### 2.3 sales_cases（销售实战案例 · mb_ai_core）

```sql
CREATE TABLE sales_cases (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    submitted_by        VARCHAR(128) NOT NULL,  -- 销售企微 UserId
    raw_chat_log        TEXT NOT NULL,          -- 原始聊天记录

    -- AI 结构化提炼结果
    customer_type       VARCHAR(255),
    core_objection      TEXT,
    breakthrough_logic  TEXT,
    follow_up_script    TEXT,
    extracted_summary   JSON,

    status              ENUM('PENDING','APPROVED','REJECTED') NOT NULL DEFAULT 'PENDING',
    knowledge_item_id   BIGINT REFERENCES knowledge_items(id) ON DELETE SET NULL,

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    approved_at         DATETIME,
    approved_by         VARCHAR(128)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_sales_cases_status ON sales_cases(status);
CREATE INDEX idx_sales_cases_submitted_by ON sales_cases(submitted_by);
CREATE INDEX idx_sales_cases_knowledge_item_id ON sales_cases(knowledge_item_id);
```

## 3. 状态机

### knowledge_items
```
DRAFT --(提交审核)--> PENDING --(approve)--> APPROVED --(sync OK)--> vector_doc_id 回填
                       |
                       └--(reject)--> REJECTED
```

### unanswered_questions
```
OPEN --(运营补答案并审核)--> RESOLVED (knowledge_item_id 回填)
   |
   └--(判定为噪声/重复)--> IGNORED
```

### sales_cases
```
PENDING --(approve)--> APPROVED --(sync OK)--> knowledge_item_id 回填
   |
   └--(reject)--> REJECTED
```

## 4. 与 Dify 向量库的对应关系（engine_rag）

| mb_ai_core 表 | Dify Dataset | 说明 |
|---|---|---|
| `knowledge_items` where `domain='CS'` | `CS_KB` | 客服 FAQ 与标准答案 |
| `knowledge_items` where `domain='SALES'` | `Sales_KB` | 销售话术与成交案例 |
| `knowledge_items` where `domain='DOCTOR'` | 可复用 `CS_KB` 或单独 `Medical_KB` | 医疗专业内容，需医师审核 |

同步原则：
1. 只同步 `status='APPROVED'` 的项。
2. 同步前检查 `vector_doc_id`：为空则创建文档，非空则更新文档。
3. 同步失败必须记录结构化日志并保留重试可能（status 保持 APPROVED，通过失败日志告警）。

---

# v2 微信 AI 客服：对话日志湖与会话控制表（2026-08-03 修订为 MySQL 双库）

> 适用范围：微信 AI 客服平台 v2（`.agent/designs/wechat-ai-cs-v2.md`），数据库 `mb_ai_cs`。
> 原则：`cs_chat_logs` 是全量对话日志湖（Data Lake），任何一条用户消息与 AI 回复**必须**落库；
> `session_state` 是 Redis 会话状态机的持久化镜像（运行时以 Redis 为准，TTL 过期后从本表重建）；
> `leads_preview` 为线索预备表，由低置信度/高意向对话异步生成，供销售跟进。

## 5. channel 枚举（四类微信入口）

```sql
-- MySQL 枚举值（列内联 ENUM，无需独立 CREATE TYPE）
-- 'WXKF'        = 企微「微信客服」单聊
-- 'MP'          = 公众号 / 服务号
-- 'H5'          = 官网聊天窗口（自建 H5）
-- 'WECOM_GROUP' = 企微群机器人（按群维度建会话，非 @ 消息静默）
```

## 6. 三张新表 DDL（mb_ai_cs）

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

CREATE INDEX idx_cs_chat_logs_session_id ON cs_chat_logs(session_id);
CREATE INDEX idx_cs_chat_logs_open_id ON cs_chat_logs(open_id);
CREATE INDEX idx_cs_chat_logs_created_at ON cs_chat_logs(created_at);
CREATE INDEX idx_cs_chat_logs_channel_created ON cs_chat_logs(channel, created_at);
CREATE INDEX idx_cs_chat_logs_hit_human ON cs_chat_logs(hit_human);
-- 高并发写入场景建议按时间分区（后续按月 range partition），MVP 阶段先不分区
```

### 6.2 session_state（会话状态表）

```sql
CREATE TABLE session_state (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL UNIQUE,   -- 与 cs_chat_logs.session_id 一致
    channel         ENUM('WXKF','MP','H5','WECOM_GROUP') NOT NULL,
    open_id         VARCHAR(128) NOT NULL,

    state           ENUM('NORMAL','HUMAN_MODE','BLOCKED') NOT NULL DEFAULT 'NORMAL',
    context         JSON,                           -- 上下文快照（最近 N 轮摘要等）
    negative_streak SMALLINT NOT NULL DEFAULT 0,    -- 连续负面情绪计数（>=2 转 HUMAN_MODE）

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    expires_at      DATETIME                        -- 会话过期时间（与 Redis TTL 对齐）
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_session_state_open_id ON session_state(open_id);
CREATE INDEX idx_session_state_state ON session_state(state);
```

### 6.3 leads_preview（线索预备表）

```sql
CREATE TABLE leads_preview (
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

CREATE INDEX idx_leads_preview_status ON leads_preview(status);
CREATE INDEX idx_leads_preview_open_id ON leads_preview(open_id);
CREATE INDEX idx_leads_preview_created_at ON leads_preview(created_at);
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
- `session_state` 表为持久化镜像：Redis 丢失或过期后从表重建；`state` 变化时同步写表。
- `HUMAN_MODE` 下 AI 停止抢答；`BLOCKED` 下仅输出固定安全话术并转人工。

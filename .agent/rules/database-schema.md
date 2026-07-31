# 数据库表结构设计 (.agent/rules/database-schema.md)

> 适用范围：gut-health-agent-platform MVP 阶段。  
> 原则：PostgreSQL 为唯一事实来源；所有知识项、未解答问题、销售案例均在此控制生命周期，再经 Sync Engine 异步同步到 Dify Vector DB。

## 1. 核心实体关系

```
knowledge_items  ──<  unanswered_questions  (cs_gap_id 可选外键)
   │
   │  domain='SALES' 时可选关联
   └──>  sales_cases  (sales_case_id 可选外键)
```

- 客服缺口答案与销售案例最终都会沉淀为 `knowledge_items` 中的一条知识。
- `sales_cases` 单独保存原始聊天记录与 AI 提炼结果，便于运营二次校验。

## 2. DDL

### 2.1 knowledge_items（统一知识库 / 话术库）

```sql
CREATE TYPE knowledge_domain AS ENUM ('CS', 'SALES', 'DOCTOR');
CREATE TYPE knowledge_source_type AS ENUM ('MANUAL', 'CS_GAP', 'SALES_CASE');
CREATE TYPE knowledge_status AS ENUM ('DRAFT', 'PENDING', 'APPROVED', 'REJECTED');

CREATE TABLE knowledge_items (
    id              SERIAL PRIMARY KEY,
    domain          knowledge_domain NOT NULL DEFAULT 'CS',
    source_type     knowledge_source_type NOT NULL DEFAULT 'MANUAL',
    status          knowledge_status NOT NULL DEFAULT 'PENDING',

    title           VARCHAR(255) NOT NULL,
    question        TEXT,                       -- 客服 FAQ 的“问题”
    answer          TEXT NOT NULL,              -- 标准答案 / 销售话术正文
    tags            VARCHAR[] DEFAULT '{}',

    -- 外键：当 source_type='CS_GAP' 时记录来源缺口
    cs_gap_id       INTEGER REFERENCES unanswered_questions(id) ON DELETE SET NULL,
    -- 外键：当 source_type='SALES_CASE' 时记录来源案例
    sales_case_id   INTEGER REFERENCES sales_cases(id) ON DELETE SET NULL,

    -- Dify 侧文档 ID，审核同步成功后回填
    vector_doc_id   VARCHAR(255),

    created_by      VARCHAR(128),               -- 运营账号 / 销售企微 UserId
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at     TIMESTAMPTZ,
    approved_by     VARCHAR(128)
);

CREATE INDEX idx_knowledge_items_domain_status ON knowledge_items(domain, status);
CREATE INDEX idx_knowledge_items_source_type ON knowledge_items(source_type);
CREATE INDEX idx_knowledge_items_vector_doc_id ON knowledge_items(vector_doc_id) WHERE vector_doc_id IS NOT NULL;
CREATE INDEX idx_knowledge_items_tags ON knowledge_items USING GIN(tags);
```

### 2.2 unanswered_questions（客服未解答问题捕获）

```sql
CREATE TYPE unanswered_status AS ENUM ('OPEN', 'PROCESSING', 'RESOLVED', 'IGNORED');

CREATE TABLE unanswered_questions (
    id              SERIAL PRIMARY KEY,
    user_query      TEXT NOT NULL,              -- 用户原始问题
    normalized_query TEXT,                      -- 归一化/去重后的问题
    context         JSONB,                      -- 可选上下文：session_id, user_id, channel
    match_score     NUMERIC(5,4),               -- Dify 返回的最佳匹配分
    status          unanswered_status NOT NULL DEFAULT 'OPEN',

    -- 关联最终生成的知识项
    knowledge_item_id INTEGER REFERENCES knowledge_items(id) ON DELETE SET NULL,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX idx_unanswered_questions_status ON unanswered_questions(status);
CREATE INDEX idx_unanswered_questions_created_at ON unanswered_questions(created_at);
CREATE INDEX idx_unanswered_questions_knowledge_item_id ON unanswered_questions(knowledge_item_id);
```

### 2.3 sales_cases（销售实战案例）

```sql
CREATE TYPE sales_case_status AS ENUM ('PENDING', 'APPROVED', 'REJECTED');

CREATE TABLE sales_cases (
    id                  SERIAL PRIMARY KEY,
    submitted_by        VARCHAR(128) NOT NULL,  -- 销售企微 UserId
    raw_chat_log        TEXT NOT NULL,          -- 原始聊天记录

    -- AI 结构化提炼结果
    customer_type       VARCHAR(255),
    core_objection      TEXT,
    breakthrough_logic  TEXT,
    follow_up_script    TEXT,
    extracted_summary   JSONB,

    status              sales_case_status NOT NULL DEFAULT 'PENDING',
    knowledge_item_id   INTEGER REFERENCES knowledge_items(id) ON DELETE SET NULL,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at         TIMESTAMPTZ,
    approved_by         VARCHAR(128)
);

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

## 4. 与 Dify 向量库的对应关系

| PostgreSQL 表 | Dify Dataset | 说明 |
|---|---|---|
| `knowledge_items` where `domain='CS'` | `CS_KB` | 客服 FAQ 与标准答案 |
| `knowledge_items` where `domain='SALES'` | `Sales_KB` | 销售话术与成交案例 |
| `knowledge_items` where `domain='DOCTOR'` | 可复用 `CS_KB` 或单独 `Medical_KB` | 医疗专业内容，需医师审核 |

同步原则：
1. 只同步 `status='APPROVED'` 的项。
2. 同步前检查 `vector_doc_id`：为空则创建文档，非空则更新文档。
3. 同步失败必须记录结构化日志并保留重试可能（status 保持 APPROVED，通过失败日志告警）。

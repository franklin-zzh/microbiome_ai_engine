# Dify Service API 契约（1.16.1 冻结版）

> 本文档冻结后端 `backend/app/clients/dify_knowledge_client.py` 与服务器 Dify 1.16.1 之间的
> API 契约。依据：Dify 1.16.1 源码
> （`api/controllers/service_api/dataset/document.py`、
> `api/services/entities/knowledge_entities/knowledge_entities.py`）。
>
> 实测状态：⏳ 待运行 `backend/scripts/verify_dify_contract.py`（根 `.env` 回填
> `DIFY_API_KEY` / `CS_QA_DATASET_ID` 后执行）。运行通过后本文档升级为「已实测」。
> 修改服务器 Dify 版本（大版本升级）时必须重跑该脚本并同步本文档。
>
> 写入端双路径（2026-08-12 落地）：`qa_model` 文档经 Knowledge Pipeline
> （`/datasets/{id}/pipeline/run`，见 dify_knowledge_client.py run_pipeline）；
> `text_model`/`hierarchical_model` 文档经 `create_by_file` 直传（Dify 原生切割）。

## 1. 端点总览（Service API，前缀 `{DIFY_BASE_URL}` = `http://192.168.110.16:3080/v1`）

鉴权：`Authorization: Bearer {DIFY_API_KEY}`（Service API 密钥，知识库管理权限）。

| 操作 | 方法 | 路径 | 成功码 | 说明 |
|---|---|---|---|---|
| 创建知识库 | POST | `/datasets` | 200 | 建库时定 `indexing_technique` / `retrieval_model` |
| 文本建文档 | POST | `/datasets/{id}/document/create_by_text`（legacy，后端现用）<br>`/datasets/{id}/documents/create-by-text`（canonical） | 200 | 返回 `document.id` + `batch` |
| 文件建文档 | POST | `/datasets/{id}/document/create_by_file` | 200 | multipart；`data` 字段为 JSON 字符串（后端 `dify_knowledge_client.create_by_file` 已实现，CS_DOC 直传路径） |
| 文档详情 | GET | `/datasets/{id}/documents/{doc_id}` | 200 | 含 `indexing_status` / `display_status` / `doc_form` |
| 索引状态 | GET | `/datasets/{id}/documents/{doc_id}/indexing-status` | 200 | `indexing_status` + `completed_segments` / `total_segments` |
| 文本更新 | PUT | `/datasets/{id}/documents/{doc_id}` | 200 | 重新触发索引；`name`+`text` 必填 |
| 删除文档 | DELETE | `/datasets/{id}/documents/{doc_id}` | **204** | 永久删除文档与全部 chunk（见 §4 限制） |
| 原始文件下载 | GET | `/datasets/{id}/documents/{doc_id}/download` | 200 | 返回签名下载 URL |
| 检索 | POST | `/datasets/{id}/retrieve` | 200 | 工作流知识检索节点底层调用（读取端，与写入 payload 无关） |

> 注：canonical 路径用连字符（`documents/create-by-text`），legacy 下划线别名仍注册
> （源码注释明确 "Legacy underscore aliases remain registered for backward compatibility"）。
> 实测脚本会同时探测两条路径，后端保持 legacy 路径（兼容老版本）并记录结果。

## 2. create_by_text payload 契约（字段与枚举）

```json
{
  "name": "文档名",
  "text": "文档全文（qa_model 时建议为 问题：…\n\n答案：… 对）",
  "indexing_technique": "high_quality",          // 枚举: high_quality | economy
  "doc_form": "qa_model",                         // 枚举: text_model | hierarchical_model | qa_model
  "doc_language": "Chinese",
  "process_rule": { "mode": "automatic" },        // 可选；automatic | custom | hierarchical
  "retrieval_model": {                            // 可选；省略则继承知识库检索配置
    "search_method": "semantic_search",           // semantic_search | full_text_search | hybrid_search
    "reranking_enable": false,
    "top_k": 3,
    "score_threshold_enabled": true,
    "score_threshold": 0.65
  },
  "embedding_model": null,                        // 可选；省略用知识库默认 embedding
  "embedding_model_provider": null
}
```

### 关键约束

- `indexing_technique`：**向空知识库添加第一个文档时必填**；之后可省略（继承知识库设置）。
- `process_rule`：**create_by_file 必填**（实测 1.16.1 缺失返回 400 `invalid_param "process_rule is required."`；
  create_by_text 省略不报错）。`{"mode": "automatic"}` = Dify 默认切分规则，custom/hierarchical 可覆盖。
- `doc_form` 枚举仅 `text_model` / `hierarchical_model` / `qa_model`（`DocForm` 校验，
  非法值 400 `invalid_param`）。
- `retrieval_mode` **不是** create_by_text 的字段（pydantic 忽略未知字段，不报错但无效）；
  检索方式在**创建知识库**（`POST /datasets`）时配置，或通过 `retrieval_model` 对象覆盖。

### 后端现状修正对照（dify_client.py 需修复的点）

| 字段 | 旧值（有误） | 修正值 | 后果说明 |
|---|---|---|---|
| `doc_form` | `"qa"` | `"qa_model"` | `"qa"` 不在枚举内 → 400，同步必失败 |
| `retrieval_mode` | `"semantic"` | 删除该字段 | 1.16.1 忽略未知字段，不生效；检索方式由知识库配置决定 |
| `indexing_technique` | `"high_quality"` | 不变 ✅ | 合法 |
| `doc_language` | `"Chinese"` | 不变 ✅ | 合法 |

## 3. 异步行为（200 ≠ 索引完成）

- `create_by_text` 返回 200 仅代表 Dify 已接收，实际切分/embedding 走 celery 异步队列。
- 索引状态经 `GET .../documents/{doc_id}` 的 `indexing_status` 轮询：
  `queuing → indexing → completed | error`（另有 `paused` / `disabled` / `archived`）。
- 后端同步闭环必须：保存 `document.id` → 状态置 `INDEXING` → 轮询至 `completed` 才置
  `COMPLETED`；`error` 时记录 `error` 字段并允许重试。
- 删除文档若处于 indexing 中返回 400 `document_indexing`，重试策略需处理。

## 4. 删除限制（reject/revoke 依赖）

| 场景 | 状态码 | 处理 |
|---|---|---|
| 文档不存在 | 404 `not_found` | 视为已删除，仅清本地 `vector_doc_id` |
| 索引中删除 | 400 `document_indexing` | 置对账任务延迟重试 |
| archived 文档 | 403 `archived_document_immutable` | 需先恢复文档（控制台操作），人工介入 |

## 5. 检索契约（读取端，工作流知识检索节点）

- 工作流内知识检索节点（`cs_agent_chatbot.yml`）：`dataset: CS_QA`（semantic，top_k 3，threshold 0.65）
  + `dataset: CS_DOC`（hybrid，top_k 5，threshold 0.3）双库检索，合并后经 rerank 节点精排
  （`bge-reranker-v2-m3`，需 Dify 模型供应商配置）——这是**读取端**配置，
  与写入端 `doc_form` 解耦：qa_model 与 text_model 文档分库存放，由各自检索节点召回。
- 双库已落地（2026-08-12）：写入端 payload 结构不变，只换 `dataset_id` 与 `doc_form`；
  目标库未配置时拒绝发布（不回落另一库，避免 QA 与文本混库）。

## 6. 待实测确认项（2026-08-14 实测回填，`backend/scripts/verify_dify_contract.py`）

- [x] legacy 下划线路径仍可用（后端保持 `document/create_by_text`）——实测 200（qa_model/text_model 均走 legacy 路径成功）
- [x] `qa_model` 实际索引耗时与 `indexing_status` 流转——实测：创建 200 → `splitting`（约 15s）→ `completed`
- [x] `text_model` 实际索引耗时与流转——实测（create_by_text 写入 CS_DOC 库）：`splitting` → `completed`；**create_by_file 直传路径留待 Phase 3 入库时实测**
- [x] DELETE 后 GET 返回 404 的确认——实测 DELETE 204 → GET 404 `not_found`
- [x] 形态不匹配拒绝——向 qa_model 库（Q&A for CS）写 text_model 文档 → 400 `invalid_param "doc_form is different from the dataset doc_form."`（一库一形态的预期安全行为）
- [ ] `doc_language: Chinese` 对切分的影响（对比 English 默认值）——未测

# cs workflow 跑通操作手册（Dify 控制台侧）

> 本手册面向「在 Dify 控制台把客服 Agent 工作流跑通」的**人工操作**部分。
> 由 AI 侧（Reasonix）准备素材与脚本，控制台操作由人工执行；执行结果回填 §8 基线表。

## 0. 已确认的前置（AI 侧探测结果，2026-08 校验）

- Dify 服务器 `http://192.168.110.16:3080` 可达，Service API 鉴权通过。
- 两个知识库已存在（**控制台显示名** → ID → doc_form）：
  | 控制台显示名 | Dataset ID | doc_form | 用途 |
  |---|---|---|---|
  | `Q&A for CS` | `6f52cc8b-ce18-43e7-800b-5005f203254e` | `qa_model` | 问答库（代码/配置里叫 `CS_QA`） |
  | `General for CS` | `60c9bb5b-35a9-40d0-a9f6-30b4fb9ed90f` | `text_model` | 长文档/通用库（代码/配置里叫 `CS_DOC`） |
- 根目录 `.env` 已回填：`CS_QA_DATASET_ID` / `CS_DOC_DATASET_ID` / `DIFY_BASE_URL` / `DIFY_API_KEY` / `DIFY_KNOWLEDGE_API_KEY`。
- ⚠️ ~~已知问题：`verify_dify_contract.py` 探测 `GET /v1/datasets/{id}` 返回 502~~ → **已定位并修复（2026-08-14）**：① 脚本 `httpx` 未设 `trust_env=False`，走了本机系统代理导致 502/超时；② 用 `DIFY_API_KEY`（app- 前缀应用密钥）调知识库端点返回 401 属 Dify 权限隔离（非 key 失效）。修复后 CONTRACT OK（qa_model/text_model 创建-索引-删除全链路通过）。

## 1. 模型供应商配置确认

Dify 控制台 → 右上角头像 → **设置 → 模型供应商**，确认三项齐备：

- [ ] **LLM**：`DeepSeek-V3`（或等效）已配置 API Key（工作流 `answer_llm` / `risk_guard` 节点用）
- [ ] **Rerank**：`bge-reranker-v2-m3` 已配置（rerank 节点必需；没有就用 `bge-reranker-large` 等 rerank 模型替代，并把工作流 rerank 节点模型同步改掉）
- [ ] **Embedding**：已配置（知识库索引必需；`Q&A for CS` / `General for CS` 建库时用的哪个模型就用哪个）

## 2. 导入工作流 DSL

1. Dify 控制台 → **创建应用 → Chatbot**（空白应用即可）。
2. 右上角 `...` → **导入 DSL** → 选择 `dify/workflows/cs_agent_chatbot.yml`。
3. 若导入报错（Dify 各版本对 `code` / `rerank` 节点字段有差异）：
   - 把报错信息贴回给 Reasonix 修 DSL；或
   - 创建空白 Chatbot 后按 `dify/workflows/README.md` 手动重建节点（本文件 §3 是节点清单）。

## 3. 导入后节点核对清单（绑定双库）

| 节点 | 类型 | 关键配置（务必核对） |
|---|---|---|
| `risk_guard` | LLM | 模型 `DeepSeek-V3`，system 为安全分类器（高风险词清单） |
| `risk_branch` | 条件 | `risk_flag == 'true'` → `risk_handoff`；否则 → `knowledge_retrieval_qa` |
| `knowledge_retrieval_qa` | 知识检索 | **dataset = `Q&A for CS`**（6f52cc8b），检索 `semantic`，top_k 3，score_threshold **0.65** |
| `knowledge_retrieval_doc` | 知识检索 | **dataset = `General for CS`**（60c9bb5b），检索 `hybrid`，top_k 5，score_threshold **0.3** |
| `merge_docs` | Code (python3) | 合并两路，给每项带 `source`（CS_QA / CS_DOC） |
| `rerank` | Rerank | 模型 `bge-reranker-v2-m3`，query=`start.query`，documents=`merge_docs.merged`，top_n 5，score_threshold 0.3 |
| `score_branch` | 条件 | rerank 结果非空 → `answer_llm`；否则 → `fallback_handoff` |
| `answer_llm` | LLM | 模型 `DeepSeek-V3`，temperature 0.3，max_tokens 1024 |
| `risk_handoff` / `fallback_handoff` | 模板 | 转人工 / 兜底话术 |
| `risk_callback` / `fallback_callback` | HTTP | POST `{{BACKEND_BASE_URL}}/api/v1/cs/unanswered/capture`，Header `X-API-Key: {{INTERNAL_API_KEY}}` |

## 4. 工作流环境变量

应用 → 编排页 → 环境变量（或 DSL 导入时设置），两个必填：

- [ ] `BACKEND_BASE_URL`：能访问到后端 FastAPI 的地址，例如
  - 后端部署在服务器同网段：`http://192.168.110.42:8000`
  - 后端在本地 + frp 内网穿透：填 frp 映射的域名
  - 联调期也可以先用 `http://192.168.110.16:3080` 前先确认后端地址再填
- [ ] `INTERNAL_API_KEY`：与根目录 `.env` 的 `INTERNAL_API_KEY` **完全相同**（从 `.env` 复制，勿贴错）

> 注意：workflow 跑在**服务器** Dify 上，`host.docker.internal` 只对容器有效，回调要指向后端实际可达地址。

## 5. 种子数据入库（二选一）

### 方案 A：控制台直传（最快，推荐冒烟用）
- `Q&A for CS` 知识库 → **添加文本**（或上传 `docs/smoke-seed/cs_qa_faq_种子.txt`）→ 触发 Pipeline 生成 QA 对 → 等索引 `completed`。
- `General for CS` 知识库 → **上传文件** → 选 `docs/smoke-seed/cs_general_肠道菌群检测流程.md` → 原生切割 → 等索引 `completed`。

### 方案 B：正规链路（走后端审核发布）
1. 本地起后端：`cd backend && .venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000`。
2. 打开 `http://localhost:8000/admin`，上传 `docs/smoke-seed/` 下文件：md → 形态选「文本切割」（text_model → General 库）；FAQ → 形态选「QA 生成」（qa_model → Q&A 库）。
3. 审核通过后自动发布（qa_model 走 Knowledge Pipeline，text_model 走 create_by_file 直传）。

## 6. 跑通三条分支（应用 → 调试/预览）

| 分支 | 测试输入 | 预期结果 |
|---|---|---|
| 正常回答 | 肠道菌群检测的流程是怎样的？多久出报告？ | 引用知识库回答，结尾带免责声明 |
| 高风险转人工 | 我最近便血，肚子还一直痛，怎么办？ | 触发 `risk_handoff` 话术，并回调后端 |
| 低置信度兜底 | 量子纠缠对肠道菌群有什么影响？ | 触发 `fallback_handoff` 话术，并回调后端 |

验证回调：后端日志出现 `POST /api/v1/cs/unanswered/capture → 200`（失败会 401/403，先查 `INTERNAL_API_KEY` 是否一致）。

## 7. 记录基线

每条测试输入记录（复制下表，回填后回传给 Reasonix）：

| 输入 | 分支 | 首 token 延迟 | 全链路耗时 | token 消耗(约) | 回答是否符合预期 | 问题描述 |
|---|---|---|---|---|---|---|
| 正常问题1 | 正常 | | | | | |
| 高风险问题 | 转人工 | | | | | |
| 低置信度问题 | 兜底 | | | | | |

## 8. 素材文件清单

- `docs/smoke-seed/cs_general_肠道菌群检测流程.md` — General 库种子（干净 md）
- `docs/smoke-seed/cs_qa_faq_种子.txt` — Q&A 库种子（FAQ 问答对）

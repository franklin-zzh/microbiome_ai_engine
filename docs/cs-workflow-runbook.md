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

## 3. 导入后节点核对清单（画布中文标题 vs 内部 ID）

| 画布显示的中文标题 | 节点内部 ID | 类型 | 关键配置与说明 |
|---|---|---|---|
| **「开始」** | `start` | Start | 起始入口，接收 `sys.query` |
| **「风险语义兑底」** | `risk_guard` | LLM | 模型 `deepseek-v4-flash`，安全分类判定（高风险输出 TRUE，否则 FALSE） |
| **「LLM结果提取」** | `1786676257638` | Code (python3) | 剥离 `risk_guard` 思考内容 `<think>...</think>`，输出 `risk_flag` |
| **「是否高风险」** | `risk_branch` | If-Else | `risk_flag` 包含 TRUE → `risk_handoff`；否则 → `normalize_query` |
| **「术语归一化」** | `normalize_query` | Code (python3) | 单次扫描正则，将小写缩写（fmt, asd, ibd, sibo等）和同义词秒级规范化映射为标准全称 |
| **「多轮指代改写」** | `query_rewriter` | LLM | 模型 `deepseek-v4-flash`，**挂载 10 轮记忆**，结合历史消解代词/补全主语（如“适合什么年龄”→“肠道菌群移植(FMT)适合什么年龄段的人群与适应症”） |
| **「检索词提取」** | `extract_query` | Code (python3) | 剥离改写模型的 `<think>` 思考标签，输出纯净的独立 Query `search_query` |
| **「检索 QA 知识库」** | `knowledge_retrieval_qa` | 知识检索 | **dataset = `Q&A for CS`**（6f52cc8b），输入变量 `extract_query.search_query`，内置 `Qwen3-Reranker-0.6B`，top_k 3，阈值 **0.65** |
| **「检索 DOC 知识库」** | `knowledge_retrieval_doc` | 知识检索 | **dataset = `General for CS`**（60c9bb5b），输入变量 `extract_query.search_query`，内置 `Qwen3-Reranker-0.6B`，top_k 5，阈值 **0.3** |
| **「合并排序」** | `merge_docs` | Code (python3) | 合并 QA 与 DOC 两路结果，按 rerank score 降序排序，输出 `merged` 与 `top_score` |
| **「是否有可用结果」** | `score_branch` | If-Else | `top_score >= 0.5` → `answer_llm`；否则 → `fallback_handoff` |
| **「客服回答」** | `answer_llm` | LLM | 模型 `deepseek-v4-flash`，基于检索参考内容作答，**开启 10 轮记忆**，输出变量为 `text` |
| **「LLM结果提取 (1)」** | `17866912012510` | Code (python3) | 输入 `answer_llm.text`，剥离思考内容 `<think>...</think>`，输出 `answer` |
| **「正常应答」** | `answer_main` | Answer | 引用 `{{#17866912012510.answer#}}` 直出回答 |
| **「低置信度转人工话术」** / **「高风险转人工话术」** | `fallback_handoff` / `risk_handoff` | Template | 低置信度兜底 / 高风险拦截话术 |
| **「低置信度回调」** / **「高风险回调」** | `fallback_callback` / `risk_callback` | HTTP | POST `{{BACKEND_BASE_URL}}/api/v1/cs/unanswered/capture`，落库未解答/高危问题 |
| **「低置信度应答」** / **「高风险应答」** | `answer_fallback` / `answer_risk` | Answer | 输出对应的兜底或拦截话术 |

## 4. 工作流环境变量

应用 → 编排页 → 环境变量（或 DSL 导入时设置），两个必填：

- [ ] `BACKEND_BASE_URL`：能访问到后端 FastAPI 的地址，例如
  - 后端部署在服务器同网段：`http://192.168.110.42:8000`
  - 后端在本地 + frp 内网穿透：填 frp 映射的域名（如 `https://wx.fmtcloud.cn`）
- [ ] `INTERNAL_API_KEY`：与根目录 `.env` 的 `INTERNAL_API_KEY` **完全相同**（从 `.env` 复制，勿贴错）

## 5. 多轮会话与记忆测试要点

> ⚠️ **常见误区**：在画布中央或某个节点上点击「运行 (Run)」属于**单次无状态调试**，不会携带任何前文会话。测试多轮记忆必须按以下步骤进行：

1. **控制台预览窗口测试**：
   - 打开 Dify 应用编排界面右上角的 **「预览与调试」对话窗口**（聊天气泡形式）；
   - 第 1 轮输入：`什么是粪菌移植`（等待 AI 完整回答）；
   - **在同一窗口中**，第 2 轮继续输入：`适合什么年龄`；
   - 验证：系统自动识别上文并精准回答 3-12 岁孤独症患儿等适用年龄，不再触发低置信度兜底。
2. **微信端/API 真机测试**：
   - 后端已在 Redis 自动按 `session_id` 维持 `conversation_id` 续聊上下文；
   - 微信客户连续追问时，后端自动携带 `conversation_id` 调 Dify API，全链路自动生效多轮改写与记忆。

## 6. 种子数据入库（二选一）

### 方案 A：控制台直传（最快，推荐冒烟用）
- `Q&A for CS` 知识库 → **添加文本**（或上传 `docs/smoke-seed/cs_qa_faq_种子.txt`）→ 触发 Pipeline 生成 QA 对 → 等索引 `completed`。
- `General for CS` 知识库 → **上传文件** → 选 `docs/smoke-seed/cs_general_肠道菌群检测流程.md` → 原生切割 → 等索引 `completed`。

### 方案 B：正规链路（走后端审核发布）
1. 本地起后端：`cd backend && .venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000`。
2. 打开 `http://localhost:8000/admin`，上传 `docs/smoke-seed/` 下文件：md → 形态选「文本切割」（text_model → General 库）；FAQ → 形态选「QA 生成」（qa_model → Q&A 库）。
3. 审核通过后自动发布（qa_model 走 Knowledge Pipeline，text_model 走 create_by_file 直传）。

## 7. 跑通测试场景

| 场景 | 测试输入 | 预期结果 |
|---|---|---|
| 缩写归一化 | fmt适合什么年龄段 | 自动归一化为 FMT 规范词，正常回答适用年龄段 |
| 多轮追问 | (前文: 什么是粪菌移植) 后续追问: 适合什么年龄 | 自动结合前文改写为完整 Query，命中知识库并正常回答 |
| 高风险转人工 | 我最近便血，肚子还一直痛，怎么办？ | 触发 `risk_handoff` 话术，并回调后端 |
| 低置信度兜底 | 量子纠缠对肠道菌群有什么影响？ | 触发 `fallback_handoff` 话术，并回调后端 |
| 极速问候 | 你好 / 在吗 / 菜单 | 后端 Fast-Path <50ms 直发欢迎语与互动菜单（不调 Dify） |

## 8. 素材文件清单

- `docs/smoke-seed/cs_general_肠道菌群检测流程.md` — General 库种子（干净 md）
- `docs/smoke-seed/cs_qa_faq_种子.txt` — Q&A 库种子（FAQ 问答对）


# CS 客服工作流冒烟基线（2026-08-14）

> 目的：记录 cs_agent_chatbot 工作流「跑通」阶段的三分支验证结果、知识库现状、
> 契约实测结论与遗留问题，作为后续 ETL 入库与效果调优（Phase 4）的对照基线。

## 1. 环境

- Dify 1.16.1 私有化 @ `http://192.168.110.16:3080`（nginx 统一入口）
- 工作流：`dify/workflows/cs_agent_chatbot.yml`（双库检索 + Rerank，DeepSeek-V3 + bge-reranker-v2-m3）
- 后端：FastAPI @ `http://host.docker.internal:8000`（workflow 内 BACKEND_BASE_URL），回调经 `X-API-Key`（INTERNAL_API_KEY）鉴权

## 2. 知识库绑定（代码命名 vs Dify 显示名）

| 代码配置键 | dataset ID | Dify 显示名 | doc_form | 用户口语 |
|---|---|---|---|---|
| `CS_QA_DATASET_ID` | `6f52cc8b-ce18-43e7-800b-5005f203254e` | Q&A for CS | qa_model（Pipeline 模式） | cs_qa |
| `CS_DOC_DATASET_ID` | `60c9bb5b-35a9-40d0-a9f6-30b4fb9ed90f` | General for CS | text_model（Pipeline 模式） | cs_general |

> 注：用户口中的 cs_qa / cs_general 即上述两个库；代码/DSL/README 保持 `CS_QA` / `CS_DOC` 命名不变，只认 dataset ID。

## 3. 三分支验证结果（Dify 控制台手工测试，2026-08-14 用户执行）

| 分支 | 测试用例 | 结果 |
|---|---|---|
| 正常回答 | 肠道菌群检测的流程是怎样的？多久出报告？ | ✅ 引用知识库回答，结尾带免责声明 |
| 高风险转人工 | 我最近便血，肚子还一直痛，怎么办？ | ✅ 触发 `risk_handoff` 话术，并回调后端 |
| 低置信度兜底 | 量子纠缠对肠道菌群有什么影响？ | ✅ 触发 `fallback_handoff` 话术，并回调后端 |

- 回调均成功落库（`/api/v1/cs/unanswered/capture` → unanswered_questions）
- 知识库现状：CS_QA 与 CS_DOC 各 **2 篇**文档，能覆盖的问题有限（待 Phase 2 ETL 后扩充）

## 4. Dify Service API 契约实测（`backend/scripts/verify_dify_contract.py`）

| 契约项 | 结果 |
|---|---|
| legacy 下划线路径 `document/create_by_text` 仍可用 | ✅ 200 |
| qa_model：创建 → splitting → completed（约 15s）→ DELETE 204 → GET 404 | ✅ 全链路通过 |
| text_model：写入 CS_DOC 库（General for CS） | ✅ 创建 → splitting → completed → DELETE 204 → GET 404（create_by_text 路径；create_by_file 直传留待 Phase 3 实测） |
| 形态不匹配拒绝：向 qa_model 库写 text_model 文档 | ✅ 400 invalid_param "doc_form is different from the dataset doc_form."（预期安全行为，一库一形态） |

## 5. 发现的问题（2026-08-14）

1. **`verify_dify_contract.py` 代理问题**：`httpx.Client` 未设 `trust_env=False`，走本机系统代理导致 502/超时；已修复（加 `trust_env=False`）。
2. **Dify Service API key 分前缀分工，勿混用**：`DIFY_API_KEY=app-...`（应用密钥，只能调 `/chat-messages` 等应用端点）与 `DIFY_KNOWLEDGE_API_KEY=dataset-...`（知识库密钥，调 `/datasets` 等）权限隔离；用 app- 密钥调知识库端点返回 401 属正常隔离，**不是 key 失效**（初判有误，已更正）。知识库操作一律用 `DIFY_KNOWLEDGE_API_KEY`；应用对话用 `DIFY_API_KEY`/`DIFY_CHAT_API_KEY`（见 `dify_chat_client.py`）。
3. **发布后接口测试已做（2026-08-14 用户跑通）**：`smoke_chat.py` 三分支全部 HTTP 200，基线见 §8。
4. **🔴 回答泄漏思考内容 `<think>`**：`answer_llm`（deepseek-v4-flash，带推理）的输出直接把 `<think>...</think>` 思考过程发给用户（接口实测 answer 以 `<think>` 开头）。`risk_guard` 分支已有「LLM结果提取」code 节点剥思考内容，`answer_llm` 缺同样处理。→ 需在 `answer_llm` 后加剥离节点或换非思考模型。
5. **CS_QA 新闻条目仍在库并被误用**：接口实测正常回答把「2026年7月…1个工作日极速16S报告」新闻条目当作参考内容（E1 未解决）。

## 6. 召回基线（2026-08-14，`backend/scripts/retrieve_test.py`，hybrid_search top_k=5）

> 知识库现状：CS_DOC 1 篇（cs_general_肠道菌群检测产品介绍.md）；CS_QA 1 篇（内容为**公司新闻时间线**，非问答对）。

### CS_DOC（workflow 检索阈值 0.3，rerank 0.3）

| 高频问题 | 最高分 | 命中内容 | 结论 |
|---|---|---|---|
| 检测流程/多久出报告 | **0.90** | 产品介绍 | ✅ 强命中 |
| 采样盒怎么用 | **0.69** | 采样流程 | ✅ 命中 |
| 报告在哪里查询 | **0.56** | 检测指标科普 | ⚠️ 弱命中（内容无「查询」） |
| 检测前需要停药吗 | **0.44** | 产品介绍 | ⚠️ 弱命中 |
| 量子纠缠（无关） | **0.56** | 产品介绍 | ❌ **假阳性**：无关问题中等分 |

### CS_QA（workflow 检索阈值 0.65）

| 高频问题 | 最高分 | 命中内容 | 结论 |
|---|---|---|---|
| 检测流程/多久出报告 | **0.70** | 2026年新闻条目 | ❌ 误命中（0.70 > 0.65 阈值会把新闻当标准话术！） |
| 采样盒怎么用 | **0.37** | 新闻条目 | ❌ 无相关内容 |
| 报告在哪里查询 | **0.45** | 新闻条目 | ❌ 无相关内容 |
| 检测前需要停药吗 | **0.40** | 新闻条目 | ❌ 无相关内容 |
| 量子纠缠（无关） | **0.49** | 新闻条目 | ❌ 假阳性 |

### 结论与调优方向

1. **CS_QA 库内容严重不对**：库内是公司新闻时间线而非 FAQ 问答对，高频问题全部无法正确回答；0.65 阈值下「流程」问题 0.70 会误把新闻当标准话术。→ 需用 `etl/scripts/faq_to_md.py` 整理真实高频问答对重新入库（内容问题，非阈值问题）。接口实测佐证：正常回答引用了「2026年7月…16S报告」新闻条目。
2. **CS_DOC 内容质量尚可**：高频问题 0.44–0.90 可召回，工作流 DOC 阈值 0.3 合适。
3. **假阳性已被新版 workflow 的 Rerank 解决 ✅**：retrieve 直测（rerank 前）无关问题「量子纠缠」0.44–0.56 中等分，但新版检索节点内置 `Qwen3-Reranker-0.6B`（score_threshold 0.3/0.65）后，接口实测「量子纠缠」双库结果为空（`qa_docs=[] doc_docs=[]`，top_score=0）——rerank 正确过滤了无关内容。
4. 「报告查询」「停药」类问题命中弱 → 列入 FAQ 补强清单。

## 7. 待办与完成情况

- [x] Dify 控制台发布应用 + `smoke_chat.py` 三分支接口测试（2026-08-14 用户执行，基线见 §8）
- [x] 修复 `answer_llm` 思考内容泄漏（在 `answer_llm` 后增加 `17866912012510` 代码节点剥离 `<think>`）（2026-08-18）
- [x] 修复大小写与缩写敏感导致的召回失败（增加 `normalize_query` 单次扫描正则归一化节点）（2026-08-18）
- [x] 支持多轮对话历史与指代消解检索（增加 `query_rewriter` LLM 节点与 `extract_query`，开启 10 轮记忆）（2026-08-18）
- [x] 支持日常问候极速秒回（后端 Fast-Path，<50ms 零 LLM 成本直出）（2026-08-18）
- [ ] CS_QA 库新闻文档撤下 + 真实 FAQ 重入库（E1）
- [ ] 全量资料清洗入库（用户资料到位后执行）

## 8. 2026-08-18 升级后工作流与功能验证基线

| 验证场景 | 输入用例 | 处理链路 | 验证结果 |
|---|---|---|---|
| **大小写/缩写归一化** | `fmt适合什么年龄段` | `normalize_query` → 扩展为 `FMT 肠道菌群移植适合什么年龄段` → 检索得分 0.8+ → `answer_llm` | ✅ 正常命中并回答「3-12岁」孤独症患儿等标准话术 |
| **多轮指代消解** | 第 1 轮: `什么是粪菌移植`<br>第 2 轮: `适合什么年龄` | `query_rewriter` 结合第 1 轮记忆重写为 `肠道菌群移植(FMT)适合什么年龄段的人群与适应症` → 检索命中 | ✅ 解决无主语短句召回失败问题，精准回答 |
| **问候 Fast-Path** | `你好` / `在吗` / `菜单` | 后端 `router._process_and_reply` 识别纯问候 → 微信接口直发欢迎语 + 3项互动菜单 | ✅ 耗时 <50ms，零 LLM 成本，无多余等待 |
| **思考内容过滤** | 任意正常问答 | `answer_llm` 输出 `text` → `17866912012510` 过滤 `<think>...</think>` → `answer_main` 输出 `answer` | ✅ 回答首尾纯净，无内部思考标签暴露 |


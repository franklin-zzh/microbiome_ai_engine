# Dify Workflow 使用说明

> 目标服务器：**Dify 1.16.x**（DSL 版本 0.7.0；1.16.x 没有独立 Rerank 节点，
> rerank 能力内置于 knowledge-retrieval 节点，本仓库 DSL 已按此编写）。

## 1. 前置条件

- Dify 1.16.x 环境已就绪（Docker 私有化或局域网服务器）。
- 在 Dify 中创建两个客服知识库（Dataset）：
  - `CS_QA`：客服 Q&A 问答库（`qa_model`，启用 Knowledge Pipeline 生成 QA）
  - `CS_DOC`：客服长文档库（`text_model`/`hierarchical_model`，Dify 原生切割）
- 记录两个 Dataset 的 ID，填入后端 `.env` 的 `CS_QA_DATASET_ID` 和 `CS_DOC_DATASET_ID`，
  并在 DSL 中替换两处 knowledge-retrieval 节点的 `dataset_ids`。
- 双库 Rerank 需要 Dify「模型供应商」中配置 rerank 模型（如 `bge-reranker-v2-m3`）；
  未配置时把 DSL 中 `reranking_enable` 改为 `false` 即可导入运行。
- 后端服务已启动：`http://localhost:8000`（容器内为 `host.docker.internal:8000`）。
- 企微对话链路（后端转发）需要后端 `.env` 配置：
  - `DIFY_CHAT_API_KEY`：Dify 客服应用「API 访问」页创建的对话 Key（不同于知识库 Key）
  - `WXKF_CORP_ID / WXKF_SECRET / WXKF_TOKEN / WXKF_ENCODING_AES_KEY`：企微微信客服凭据
  - `RISK_BLOCKED_REPLY`：命中高风险关键词的预设话术（留空用内置默认）

> 知识库写入侧（后端 `app/clients/dify_knowledge_client.py`）：
> - `qa_model` 文档（审核通过）→ 跑 CS_QA 库的 Knowledge Pipeline（FILE → QA Processor → KB）生成问答对；
> - `text_model`/`hierarchical_model` 文档（审核通过）→ `create_by_file` 直传 CS_DOC 库，Dify 原生切割；
> - 一个文档一种形态一个目标库，绝不双写；目标库未配置时审核发布被拒绝（不回落另一库）。
> 上传入口：审核后台 `http://localhost:8000/admin`（形态单选：QA 生成 / 文本切割）。

## 2. 风险防护（双层架构，第一道 0 LLM 成本）

```
用户消息（企微/公众号）
   │
   ├─► 1. 后端关键词拦截（agent_cs 状态机，RISK_KEYWORDS，确定性、0 延迟）
   │      命中 → 直接推送预设安全话术 + 日志湖 risk_flag=true（不调 Dify，0 token）
   │      未命中 → 转发 Dify 客服应用（chat-messages）
   │
   └─► 2. 工作流 risk_guard（轻量模型，语义兜底）
          捕获隐晦/同义/上下文风险表达（如"拉血""肚子疼得厉害""瘦了很多"）
          命中 → risk_handoff 话术 + 回调后端
```

- 后端拦截已实现（`backend/app/agent_cs/services.py` 的 `route_incoming`），
  企微回调 `/api/v1/wechat/kf/callback` 验签后先分流再回 success，AI 逻辑异步执行；
- 命中关键词后会话进入 BLOCKED 熔断，该会话后续消息不再进 AI；
- 工作流内 `risk_guard` 因此只承担"隐晦表达"兜底，模型用便宜的即可。

## 3. 导入工作流

1. 进入 Dify 控制台 -> 创建应用 -> 选择「导入 DSL」。
2. 选择对应的 YAML 文件：
   - `cs_agent_chatbot.yml` -> 导入为 Chatbot（双库检索 + Rerank）
   - `sales_copilot_chatbot.yml` -> 导入为 Chatbot
   - `sales_case_extractor.yml` -> 导入为 Workflow
3. 导入前按 YAML 头部注释替换占位（导入本身不报错，缺了运行时会失败/空结果）：
   - `dataset_ids`（两处）→ CS_QA / CS_DOC 真实知识库 ID
   - LLM 节点 `model.provider / model.name`（risk_guard、answer_llm）→ 工作区实际配置的模型
   - `multiple_retrieval_config.reranking_model`（两处）→ 工作区 rerank 模型
   - 环境变量 `BACKEND_BASE_URL`、`INTERNAL_API_KEY` → 实际值

> 提示：若导入报 `'list' object has no attribute 'get'`，说明 `workflow` 写成了列表而非
> `{graph: {nodes, edges}}` 结构；对照本仓库 DSL 或先导出官方空模板再改。

## 4. 关键配置参数

| 参数 | 客服 Agent（双库） | 销售 Copilot | 案例提炼 |
|---|---|---|---|
| Dataset | CS_QA（semantic）+ CS_DOC（hybrid） | Sales_KB | 无（直接提交后端） |
| TopK | QA 3 / DOC 5（各自内置 Rerank 精排） | 3 | - |
| Score Threshold | QA 0.65 / DOC 0.3 | 0.0（更宽召回） | - |
| Rerank | bge-reranker-v2-m3（检索节点内置，需环境配置） | 无 | 无 |
| 模型 | risk_guard（便宜）+ answer_llm | DeepSeek-V3 | DeepSeek-V3 |
| 温度 | 0.3 | 0.5 | 0.2 |
| 回调接口 | POST /api/v1/cs/unanswered/capture | 无 | POST /api/v1/knowledge/sales-case |

### 双库检索策略说明

- **CS_QA（语义检索）**：Pipeline 生成的 QA 对质量高、覆盖高频客服问题，作为标准话术优先；
  阈值 0.65 保证低分不误答。
- **CS_DOC（hybrid 检索）**：公司文档/产品手册/政策全文，混合检索（全文 + 语义）召回更宽
  （阈值 0.3），细节由检索节点内置 Rerank 精排后供 LLM 参考。
- **合并排序**：两路结果由 code 节点合并（带 `source` 来源标识）并按 score 降序；
  合并结果为空才转人工。搜索方式（semantic/hybrid）在知识库设置中配置，节点不指定。

## 5. 数据自进化闭环

1. 客服 Agent 双库检索 + Rerank 后仍无结果 -> 回调写入 `unanswered_questions`。
2. 运营在 Admin 后台（`http://localhost:8000/admin`）上传原始文件（QA 生成或文本切割），
   审核通过后由后端发布到对应知识库（Pipeline 或 create_by_file 直传）。
3. 销售 Copilot 使用 Sales_KB 生成话术，成交后通过案例提炼器提交 SALES_CASE。
4. 旧知识项链路（`core_knowledge_items` 手工录入 + `core_sync_tasks` 对账）已于 2026-08-12 下线，
   知识一律走「原文件 → 逻辑文档/版本 → Dify」链路。

## 6. 安全护栏

双层防护（见第 2 节），关键词清单 `RISK_KEYWORDS` 与工作流 risk_guard 提示词
对齐 `.agent/rules/medical-safety.md`。系统提示中必须包含：
- 禁止疾病诊断
- 禁止具体用药建议
- 高风险关键词转人工
- 医疗免责声明

详见 `.agent/rules/medical-safety.md`。

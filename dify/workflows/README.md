# Dify Workflow 使用说明

## 1. 前置条件

- Dify 环境已就绪（Docker 私有化或 SaaS）。
- 在 Dify 中创建两个客服知识库（Dataset）：
  - `CS_QA`：客服 Q&A 问答库（`qa_model`，启用 Knowledge Pipeline 生成 QA）
  - `CS_DOC`：客服长文档库（`text_model`/`hierarchical_model`，Dify 原生切割）
- 记录两个 Dataset 的 ID，填入根目录 `.env` 的 `CS_QA_DATASET_ID` 和 `CS_DOC_DATASET_ID`。
- 双库检索 + Rerank 需要 Dify「模型供应商」中配置 rerank 模型（如 `bge-reranker-v2-m3`），
  未配置时无法导入/运行 rerank 节点。
- 后端服务已启动：`http://localhost:8000`

> 知识库写入侧（后端 `app/clients/dify_knowledge_client.py`）：
> - `qa_model` 文档（审核通过）→ 跑 CS_QA 库的 Knowledge Pipeline（FILE → QA Processor → KB）生成问答对；
> - `text_model`/`hierarchical_model` 文档（审核通过）→ `create_by_file` 直传 CS_DOC 库，Dify 原生切割；
> - 一个文档一种形态一个目标库，绝不双写；目标库未配置时审核发布被拒绝（不回落另一库）。
> 上传入口：审核后台 `http://localhost:8000/admin`（形态单选：QA 生成 / 文本切割）。

## 2. 导入工作流

Dify 当前版本支持从 DSL 文件导入 Chatbot / Workflow 应用：

1. 进入 Dify 控制台 -> 创建空白应用。
2. 选择右上角 ... -> 导入 DSL。
3. 选择对应的 YAML 文件：
   - `cs_agent_chatbot.yml` -> 导入为 Chatbot（双库检索 + Rerank）
   - `sales_copilot_chatbot.yml` -> 导入为 Chatbot
   - `sales_case_extractor.yml` -> 导入为 Workflow
4. 导入后按 YAML 中的节点说明配置 Knowledge、HTTP 请求节点、环境变量 `BACKEND_BASE_URL`，
   并把 rerank 节点模型替换为环境里实际配置的 rerank 模型。

> 提示：Dify 各版本 DSL 字段可能有差异（尤其 code / rerank 节点）。如遇导入失败，
> 可先导出空模板，再复制本文件中的 system prompt、检索配置、HTTP 节点参数到模板中。

## 3. 关键配置参数

| 参数 | 客服 Agent（双库） | 销售 Copilot | 案例提炼 |
|---|---|---|---|
| Dataset | CS_QA（semantic）+ CS_DOC（hybrid） | Sales_KB | 无（直接提交后端） |
| TopK | QA 3 / DOC 5（合并后 Rerank top_n 5） | 3 | - |
| Score Threshold | QA 0.65 / DOC 0.3（Rerank 0.3） | 0.0（更宽召回） | - |
| Rerank | bge-reranker-v2-m3（需环境配置） | 无 | 无 |
| 模型 | DeepSeek-V3 | DeepSeek-V3 | DeepSeek-V3 |
| 温度 | 0.3 | 0.5 | 0.2 |
| 回调接口 | POST /api/v1/cs/unanswered/capture | 无 | POST /api/v1/knowledge/sales-case |

### 双库检索策略说明

- **CS_QA（语义检索）**：Pipeline 生成的 QA 对质量高、覆盖高频客服问题，作为标准话术优先；
  阈值 0.65 保证低分不误答。
- **CS_DOC（hybrid 检索）**：公司文档/产品手册/政策全文，混合检索（全文 + 语义）召回更宽
  （阈值 0.3），细节由 Rerank 精排后供 LLM 参考。
- **Rerank**：两路结果合并（带 `source` 来源标识）后重排，`top_n: 5`；结果为空才转人工。

## 4. 数据自进化闭环

1. 客服 Agent 双库检索 + Rerank 后仍无结果 -> 回调写入 `unanswered_questions`。
2. 运营在 Admin 后台（`http://localhost:8000/admin`）上传原始文件（QA 生成或文本切割），
   审核通过后由后端发布到对应知识库（Pipeline 或 create_by_file 直传）。
3. 销售 Copilot 使用 Sales_KB 生成话术，成交后通过案例提炼器提交 SALES_CASE。
4. 旧知识项链路（`core_knowledge_items` 手工录入 + `core_sync_tasks` 对账）已于 2026-08-12 下线，
   知识一律走「原文件 → 逻辑文档/版本 → Dify」链路。

## 5. 安全护栏

客服 Agent 系统提示中必须包含：
- 禁止疾病诊断
- 禁止具体用药建议
- 高风险关键词转人工
- 医疗免责声明

详见 `.agent/rules/medical-safety.md`。

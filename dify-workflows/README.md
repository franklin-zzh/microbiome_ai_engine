# Dify Workflow 使用说明

## 1. 前置条件

- Dify 环境已就绪（Docker 私有化或 SaaS）。
- 在 Dify 中创建两个 Knowledge（Dataset）：
  - `CS_KB`：客服 Q&A 话术库
  - `Sales_KB`：销售话术/案例库
- 记录两个 Dataset 的 ID，填入 `backend/.env` 的 `CS_DATASET_ID` 和 `SALES_DATASET_ID`。
- 后端服务已启动：`http://localhost:8000`

## 2. 导入工作流

Dify 当前版本支持从 DSL 文件导入 Chatbot / Workflow 应用：

1. 进入 Dify 控制台 -> 创建空白应用。
2. 选择右上角 ... -> 导入 DSL。
3. 选择对应的 YAML 文件：
   - `cs_agent_chatbot.yml` -> 导入为 Chatbot
   - `sales_copilot_chatbot.yml` -> 导入为 Chatbot
   - `sales_case_extractor.yml` -> 导入为 Workflow
4. 导入后按 YAML 中的节点说明配置 Knowledge、HTTP 请求节点、环境变量 `BACKEND_BASE_URL`。

> 提示：Dify 各版本 DSL 字段可能有差异。如遇导入失败，可先导出空模板，再复制本文件中的 system prompt、检索配置、HTTP 节点参数到模板中。

## 3. 关键配置参数

| 参数 | 客服 Agent | 销售 Copilot | 案例提炼 |
|---|---|---|---|
| Dataset | CS_KB | Sales_KB | 无（直接提交后端） |
| TopK | 3 | 3 | - |
| Score Threshold | 0.65 | 0.0（更宽召回） | - |
| 模型 | DeepSeek-V3 | DeepSeek-V3 | DeepSeek-V3 |
| 温度 | 0.3 | 0.5 | 0.2 |
| 回调接口 | POST /api/v1/cs/unanswered/capture | 无 | POST /api/v1/knowledge/sales-case |

## 4. 数据自进化闭环

1. 客服 Agent 遇到低分问题 -> 回调写入 `unanswered_questions`。
2. 运营在 Admin 后台补充答案并审核 -> 生成 `knowledge_items` APPROVED 记录。
3. 审核通过触发 BackgroundTasks -> 调用 Dify Dataset API 写入向量库。
4. 销售 Copilot 使用 Sales_KB 生成话术，成交后通过案例提炼器提交 SALES_CASE。
5. Admin 审核销售案例 -> 再次同步至 Sales_KB。

## 5. 安全护栏

客服 Agent 系统提示中必须包含：
- 禁止疾病诊断
- 禁止具体用药建议
- 高风险关键词转人工
- 医疗免责声明

详见 `.agent/rules/medical-safety.md`。

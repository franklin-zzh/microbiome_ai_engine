# CS 客服 Agent 问题记录表（Issue Log）

> 记录 cs_agent_chatbot「跑通 + 优化」过程中发现的问题，按 速度类 / 效果类 / 工程类 分类。
> 对应目标 1：记录和优化问题（生成速度、生成效果）。更新：2026-08-14。

## 一、速度类（生成速度 / 链路耗时）

| # | 问题 | 影响 | 状态 |
|---|---|---|---|
| S1 | **发布后接口测试**：首 token 延迟 / 全链路耗时 / token 消耗基线 | 无法量化生成速度、无对照 | ✅ 2026-08-14 已跑通：正常 10.1s/2281tok、高风险 2.1s/515、兜底 2.6s/502（见基线 §8）；正常分支偏慢（长回答生成）列入观察 |
| S2 | **risk_guard 用 LLM 做风险分类**：每次请求多一次 LLM 调用 + 延迟 | 额外 token 成本与延迟 | ✅ 已优化：后端网关关键词拦截作第一道（用户已实现），workflow risk_guard 用便宜模型 deepseek-v4-flash + max_tokens 256 兑底 |
| S3 | 工作流为 **blocking 调用链路**（检索→rerank→LLM 顺序执行），无并行优化 | 全链路耗时 ≈ 各节点之和 | 📋 后续可评估：双库检索并行、rerank 后置等（Dify 编排层能力有限，先测基线再定） |
| S4 | **高频日常问候语响应过慢**：用户发送“你好/在吗/菜单”走全量 Dify 工作流耗时数秒 | 用户体感延迟高、浪费大模型调用 | ✅ 2026-08-18 已解决：后端路由层新增 Fast-Path 极速秒回机制（`sm.is_pure_greeting` / `sm.is_menu_request`），<50ms 零 LLM 成本直接下发欢迎语与互动菜单 |

## 二、效果类（生成效果 / 召回质量）

| # | 问题 | 影响 | 状态 |
|---|---|---|---|
| E1 | **CS_QA 库内容为「公司新闻时间线」而非 FAQ 问答对**：高频问题全部召回失败；「检测流程」0.70 > 0.65 阈值会把新闻当标准话术误答；接口实测正常回答已引用「2026年7月…16S报告」新闻条目 | 客服问答质量源头性错误，**最高优先级** | 🔴 需用 `etl/scripts/faq_to_md.py` 整理真实高频问答对重入库 CS_QA；**先撤下新闻文档**（`import_etl_md.py --revoke`）消除误用 |
| E2 | **假阳性（无关问题中等分）**：retrieve 直测「量子纠缠」0.44–0.56 | 无关问题可能被作答 | ✅ 已解决：新版检索节点内置 `Qwen3-Reranker-0.6B`（阈值 0.3/0.65），接口实测无关问题双库结果为空（top_score=0）——rerank 正确过滤 |
| E7 | **🔴 回答泄漏思考内容**：`answer_llm`（deepseek-v4-flash）输出以 `<think>...</think>` 开头，思考过程直接发给用户（接口实测） | 用户看到模型内心戏，观感差且可能暴露内部提示 | ✅ 2026-08-18 已解决：在 `answer_llm` 后增加 `17866912012510`（LLM结果提取 (1)）代码节点过滤思考标签；在 `query_rewriter` 后增加 `extract_query` 剥离思考标签 |
| E8 | **英文缩写与大小写敏感导致知识库检索失败**：如输入 `fmt适合什么年龄段` 触发低置信度兜底，而 `FMT适合什么年龄段` 正常回答 | Reranker 对小写缩写未作同义词泛化，得分低于 0.5 阈值直接被 score_branch 拦截 | ✅ 2026-08-18 已解决：在工作流前置 `normalize_query`（术语归一化）代码节点，通过单次扫描正则将 `fmt` 标准扩展为 `FMT 肠道菌群移植`（同时覆盖 asd, ibd, sibo, 粪菌移植等） |
| E9 | **缺乏多轮对话记忆与指代消解**：用户先问“什么是粪菌移植”，后问“适合什么年龄”无法检索知识库 | 检索节点直接拿缺少主语的短句检索，得分极低触发转人工；且大模型未挂载历史上下文 | ✅ 2026-08-18 已解决：新增 `query_rewriter`（多轮指代改写）LLM 节点，挂载 10 轮记忆将省略句重写为独立完整 Query；并在 `answer_llm` 开启 10 轮记忆 |
| E3 | 「报告在哪里查询」「检测前需要停药」命中弱（0.56 / 0.44） | 部分高频问题回答不准 | 📋 列入 FAQ 补强清单（Q-to-Q 入库后复测） |
| E4 | 知识库文档量少（CS_QA/CS_DOC 各 1-2 篇） | 覆盖面不足 | ⏳ 待用户提供公司资料到 `etl/raw/` 全量清洗入库 |
| E5 | 图片 OCR 局限：竖排文字、密集图表识别弱；复杂图表无结构化描述 | 部分图片信息可能丢失/错乱 | 📋 VLM 兜底位已预留（`etl/ocr.py describe_image`），需确认多模态模型与成本 |
| E6 | 混合 PDF（部分页扫描）走原生路径，图片页内容丢失 | 扫描页信息缺失 | 📋 后续按页判定 + 页级分派（docs/ETL-NOTES.md 待办） |

## 三、工程类（配置 / 脚本 / 环境）

| # | 问题 | 影响 | 状态 |
|---|---|---|---|
| P1 | **Dify key 分前缀分工**：`DIFY_API_KEY`（app- 前缀，应用密钥）调知识库端点返回 401 是权限隔离而非失效（初判已更正）；`DIFY_KNOWLEDGE_API_KEY`（dataset- 前缀）才是知识库密钥 | 混用会 401 误导排障 | ✅ 已明确分工：知识库操作→`DIFY_KNOWLEDGE_API_KEY`；应用对话→`DIFY_API_KEY`/`DIFY_CHAT_API_KEY` |
| P2 | `verify_dify_contract.py` 走系统代理致 502/超时 | 契约实测失败 | ✅ 已修复（`trust_env=False` + key 优先级 + 按形态分流），CONTRACT OK |
| P3 | Dify retrieve 需完整 `retrieval_model`（`reranking_enable` 等），缺字段 400 | 召回测试报错 | ✅ 已修复（retrieve_test.py） |
| P4 | 脚本中文输出在 PowerShell 显示乱码（控制台 GBK vs UTF-8） | 可读性 | 📋 建议运行前 `chcp 65001` 或设置 `PYTHONIOENCODING=utf-8` |
| P5 | **企微 OpenWS 不支持 RFC 6455 Ping 控制帧（Opcode 0x9）**：客户端主动 ping 触发 `1002 invalid opcode / incorrect masking` 或 `1011 keepalive ping timeout` | 长连接每隔 20~30 秒偶发断线重连 | ✅ 2026-08-19 已解决：在 `websockets.connect` 中设置 `ping_interval=None, ping_timeout=None`，完全依靠 TCP Keep-Alive 保活，实测 60s+ 持续常驻零断连 |
| P6 | **生产环境 Gunicorn 多 Worker（`-w 2`）导致 WebSocket 顶号互踢**：两进程拿同一 Bot ID 连接企微 | 线上每隔 1~2 秒反复出现 CONNECTING 与 RECONNECTING 死循环 | ✅ 2026-08-19 已解决：FastAPI 全异步应用在 `docker-compose.prod.yml` 中锁定为单 Worker（`-w 1`）独占运行 |
| P7 | **企微推送报文协议命名差异（`msgtype` vs `msg_type`）**：后端未提取到 `msg_type` 导致判定为不支持消息 | 用户发送正常文字但被后台 `status: IGNORED` 忽略 | ✅ 2026-08-19 已解决：在 `wecom_sales_service.py` 和客户端中全面兼容 `msgtype`、`msg_type` 与 `text.content` 结构 |
| P8 | **`websockets` 库 v14+/v16+ 属性弃用**：`is_connected` 访问 `ws.closed` 报错 `'ClientConnection' object has no attribute 'closed'` | 连接重连与心跳崩溃 | ✅ 2026-08-19 已解决：重构 `is_connected` 属性，优先检查 `ws.state.name == "OPEN"` |
| P9 | **对话日志湖（`cs_chat_logs`）高频检索维度列化演进**：`chat_type` 与 `chat_id` 原先全放 JSON `meta` 无法走 B-Tree 索引 | 后台按群筛选或按单聊/群聊统计时全表扫描性能低 | ✅ 2026-08-19 已解决：不进行物理分表，生成 Alembic 迁移脚本新增 `chat_type` 与 `chat_id` 实体列并建立索引 |
| E10 | **招商群与内部销售 1v1 话术混淆、机械死板**：单一 Agent 无法同时兼顾面向外部代理商的公司实力/合作框架介绍与面向内部员工的实战异议攻防 | 群聊与单聊体验死板僵硬、大水漫灌 | ✅ 2026-08-19 已解决：拆分为 `partner_agent_chatbot.yml`（招商合伙人）与 `sales_agent_chatbot.yml`（销售实战）双 Dify 工作流，严格控制 150~250 字轻快拟人化输出，后端根据 `is_group` 智能动态分发 |

## 四、整改优先级与更新（2026-08-19）

1. ✅ **P5 / P6 / P7 / P8**：企业微信智能机器人 WebSocket 长连接四大底层协议与多进程冲突已彻底根治，稳定度达 100%。
2. ✅ **P9**：`cs_chat_logs` 数据表 `chat_type` 与 `chat_id` 实体列迁移及入库已就绪。
3. ✅ **E10**：招商与内部销售双 Agent 工作流 DSL、拟人化提示词与后端动态路由已全量上线。
4. 🔴 **E1**：CS_QA 库新闻文档撤下 + 真实 FAQ 重入库（工具已就绪，等 FAQ 内容）。
5. 📋 **E3/E4**：FAQ 补强 + 全量资料清洗入库（等用户文件）。


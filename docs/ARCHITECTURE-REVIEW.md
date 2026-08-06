# 架构审查报告 · agent_cs(微信 AI 客服 + 知识库引擎)

> 审查日期:2026-08-05 ｜ 审查范围:全仓(backend / dify / frp-client / docker-compose / .agent 设计文档)
> 项目定位:公司内部生产工具,面向(肠菌检测+调养)公司,微信生态 AI 客服(未来扩展销售/医生 AI 助手)
> 状态:第 1 周→第 2 周过渡期(W1 基础架构已交付,pytest 15/15)

---

## 一、总体评价

架构主线正确,无需要推翻的架构性错误:

- **5 秒异步回包**解耦设计正确(微信/企微回调硬约束),回调内严禁同步调 Dify 的规则已写入规范;
- **MySQL 单库 + 表名前缀隔离 + Alembic** 符合 MVP 轻量原则,跨域不建物理外键为未来拆库留路;
- **Redis 状态机 + DB 持久化镜像 + TTL 滚动刷新**的会话治理落地完整,且已有测试覆盖(DB 兜底恢复、惰性清理);
- **全量对话日志湖 + 未解答问题捕获反哺知识库**构成数据闭环,是方案最大亮点;
- **知识审核闭环**(DRAFT→PENDING→APPROVED→REJECTED + Dify 同步)方向正确;
- 医疗合规护栏(风险关键词、免责声明、HITL)设计齐备。

主要问题集中在三处:**安全基线缺失**(上线前必须补)、**Dify 同步与异步链路的可靠性缺口**、**多代理(sales/doctor)演进的前瞻性不足**。另有若干"计划内未落地"事项(微信回调、Celery 延迟到 W3),已在文中标注,不视为缺陷。

---

## 二、问题清单(按优先级)

### 🔴 P0 安全(上线前必须修复)

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 1 | **frp 真实 token 已提交进 git**:`auth.token = "FmtCloud_TOPSecret_2026_Token"` 且文件被 git 跟踪(已确认 `git ls-files` 包含 frpc.toml)。token 已进入提交历史,仅删文件无效 | `frp-client/frpc.toml:6` | 任何拿到仓库的人可连入 frp 服务端,反向进入内网。必须**轮换 token** + 移入 `.env` 插值(compose 注入)+ 后续加密钥扫描 |
| 2 | **全接口无鉴权 + CORS 全开**:`/admin/*`(知识审核)、`/chat/logs`(对话日志湖,含 open_id/聊天原文/检索切片)、`/knowledge` 写接口全部裸奔;`allow_origins=["*"]`;Dify workflow 的 `http_request` 回调 `/cs/unanswered/capture` 无任何共享密钥 | `backend/main.py:30`(CORS)、`dify/workflows/cs_agent_chatbot.yml:60` | 任何拿到服务地址的人可读全量对话(个保法风险)、伪造审核、污染知识库、耗尽 Dify 配额 |
| 3 | **微信 POST 回调是空壳**:只回 `success`,不验签、不解密、不落库、不入队。GET 校验已正确实现,POST 为占位(计划内 W3,但 frp 已把公网流量引入,补齐前该端点等于裸暴露,且即使空壳也应先做签名校验) | `backend/app/agent_cs/router.py:283-299` | 公网可伪造消息打到回调端点;上线前必须补齐验签+解密+入队 |
| 4 | **默认凭据硬编码**:`database_url` 默认 `root:fumate`、`redis_url` 默认 `:fumate` 写在代码默认值;docker-compose 用 `${VAR:-fumate}` 兜底;Dify 部署 compose 含公开默认 `DB_PASSWORD=difyai123456`、默认 `WEAVIATE_API_KEY`、`SANDBOX_API_KEY=dify-sandbox` | `backend/app/core/config.py:25,31`、`docker-compose.yml:23-24`、`dify/deploy/docker-compose.dify.yml:60,105,119` | 生产环境漏配即裸奔;默认值应改为必填(空则启动失败) |

### 🟠 P1 可靠性 / 一致性

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 5 | **Dify 同步是 fire-and-forget**:用 FastAPI `BackgroundTasks`(进程内线程)——多 worker 会重复执行、进程崩溃丢任务、无重试无告警;失败仅记日志,`status` 保持 APPROVED 但 `vector_doc_id` 为空,运营无感知 | `backend/app/knowledge/services.py:11-37`、`backend/app/knowledge/router.py:162` | 知识审核通过后可能静默不同步到向量库,线上回答缺新知识 |
| 6 | **知识生命周期与向量库不同步**:只有 approve→create/update;**REJECTED / 删除不删 Dify 文档**;Dify 侧手工删文档后 MySQL 残留 `vector_doc_id`;无对账机制 | `backend/app/clients/dify_client.py`(仅 create_by_text / put) | 已撤销/错误的知识仍可被 RAG 召回,医疗场景是合规风险 |
| 7 | **负面情绪计数是死代码**:`increment_negative_streak` 无调用方,`route_incoming` 恒返回 `negative_streak: 0`,"连续负面≥2 转人工"规则未接线(AGENT.md 待办 #8 已自知) | `backend/app/agent_cs/services.py:61,154` | "连续负面情绪转人工" HITL 规则形同虚设 |
| 8 | **DB 连接池无 `pool_pre_ping` 且未设池大小**:MySQL 8 默认 wait_timeout 8h,空闲连接被服务端断开后报 "MySQL server has gone away";高并发下默认池(5)可能不足 | `backend/app/core/database.py:11` | 长时间运行后随机 500 错误 |
| 9 | **httpx.Client 每次新建**:无连接复用;60s 超时对 embedding 大文档可能不够 | `backend/app/clients/dify_client.py:57` | 同步吞吐受限、大文档同步易超时 |
| 10 | **测试连真实本地 MySQL/Redis、无 CI**:conftest 在 import 时设置 `os.environ["DATABASE_URL"]`,依赖模块加载顺序;测试环境不可复现,无法在 CI 跑 | `backend/tests/conftest.py:22` | 环境漂移、无法自动化回归 |

### 🟡 P2 架构演进(为 sales/doctor 铺路)

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 11 | **三个 agent 域没有共享运行时**:`agent_cs` 有完整状态机+路由,`agent_sales` 仅占位(docstring)+1 个只读路由,`agent_doctor` 仅 models。未来 3 个 agent 共享的东西极多(渠道适配、Dify 调用、HITL、日志、未解答捕获、线索提取) | `backend/app/agent_{cs,sales,doctor}/` | 不抽象就是三份复制粘贴;建议抽共享 Agent Runtime(意图路由、RAG 调用、HITL、日志),各 agent 只配置 persona/知识域/工具集 |
| 12 | **微信四渠道未抽象**:WXKF(48h 会话窗口+主动推送)/ MP(被动回复+客服消息,两套 API)/ H5(自建,身份体系缺失)/ WECOM_GROUP(无状态 webhook、群维度 session、非 @ 静默)——设计评审已列,代码未体现 | `backend/app/agent_cs/router.py`(wechat_router 仅占位) | W3 接入时四套逻辑堆在一个 router 里;建议统一 `ChannelAdapter` 接口(verify/decrypt/receive/send/push),按 channel 分派 |
| 13 | **Dify workflow DSL 难版本化**:`dify/workflows/*.yml` 是手写近似 DSL(注释自认"导入时需按当前版本替换字段");workflow 内 `BACKEND_BASE_URL` 硬编码 `host.docker.internal`、回调无鉴权;`risk_guard` 用 **LLM 做风险分类**——每次请求额外 token 开销且可能误判,网关层确定性关键词护栏应作为第一道防线 | `dify/workflows/cs_agent_chatbot.yml:15,30-39,60` | 配置漂移、重复风控成本;建议网关先做关键词拦截,LLM 分类仅作兜底 |
| 14 | **core 三表循环外键**:`core_knowledge_items ↔ core_unanswered_questions / core_sales_cases` 成环(测试 drop 需 `SET FOREIGN_KEY_CHECKS=0`),与"跨域不建 FK"原则不一致 | `backend/app/knowledge/models.py:69-70`、`backend/tests/conftest.py:36-51` | 未来拆库/改表被外键缠住;既然目标拆库,core 内部也应改为逻辑关联 |
| 15 | **日志湖无分区无归档**:`cs_chat_logs` 单表无限增长,`retrieved_chunks` JSON 可能很大;设计已提"按月 range partition"未落地;表含个人信息(open_id、聊天内容),需保留策略+脱敏(个保法) | `backend/app/agent_cs/models.py:35-58`、`.agent/rules/database-schema.md:200` | 表膨胀拖慢写入与查询;合规风险 |
| 16 | **Redis 单例与多进程**:`redis.py` 模块级单例,uvicorn `--workers>1` / Celery worker 场景下连接池行为未验证 | `backend/app/core/redis.py:12` | 多进程场景需验证,必要时按进程初始化 |

### 🟢 P3 工程化 / 体验

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 17 | 依赖无锁文件(`requirements.txt` 区间版本,镜像 `python:3.11-slim` 不固定 patch) | `backend/requirements.txt`、`backend/Dockerfile:1` | 构建不可复现;建议 uv/pip-tools 锁定 + 镜像 digest 固定 |
| 18 | 可观测性仅 stdout JSON 日志:无 metrics(请求量/延迟/Dify 耗时/token 消耗/队列深度/错误率)、无告警;6 周验收含"72 小时稳定性",缺告警难以保障 | `backend/app/core/logging.py` | 故障发现靠人肉看日志;建议 Prometheus + 集中日志(Loki/ELK)+ 告警规则 |
| 19 | `ROOT_ENV_FILE = parents[3]` 依赖目录层级,打包/容器内路径变化即失效;`DIFY_BASE_URL` 默认指向云版 `api.dify.ai` 而实际部署是局域网 `192.168.110.16:5081`,默认值误导 | `backend/app/core/config.py:8,37` | 部署环境差异踩坑 |
| 20 | 代码小问题:router 内局部 `from fastapi import Response`;`wechat_router` 以 `/api/v1` 和 `""` 双前缀重复挂载(兼容旧路径,需文档化);`list_unanswered`/`resolve_unanswered` 参数未用 Query 校验;`UnansweredQuestion.match_score` 存 String 而 `CsChatLog` 用 Numeric,不一致 | `backend/app/agent_cs/router.py:271,285,42`、`backend/app/knowledge/router.py:221-244`、`backend/app/knowledge/models.py:97` | 可读性/一致性小问题 |
| 21 | **git 工作区有未提交的大重构**:`dify-workflows/`、`docker-compose.dify.yml`、`DEPLOY-DIFY.md` 已删除,新 `dify/` 目录(deploy/docs/workflows)未跟踪 | `git status`(D 状态 ×6 + `?? dify/`) | 重构成果未入库,存在丢失风险;尽快提交 |
| 22 | 满意度收集交互未定义(设计评审待确认 #4);转人工后的人工通知/接管页面未设计 | `.agent/design-review.md` 四-4 | W5 落地前需业务拍板 |

---

## 三、改进方案(三层鉴权 · 已确认)

```
外部请求入口 ── FastAPI 拦截器(全局依赖,按来源分流)
  1. 人类用户 (Admin / 医生 / 销售)  → 轻量 JWT + 角色(admin 操作记审计日志)
  2. Dify Workflow 工具回调          → Header API Key / Shared Secret(密钥走 .env 注入)
  3. 微信公众平台 Webhook            → 官方 Token 签名验证(WXBizMsgCrypt,POST 补齐验签+解密)
```

- 拦截器作为 FastAPI 全局依赖实现,按路由 tag/prefix 声明鉴权要求;
- 审计日志:所有 admin 写操作(审核、驳回、删除)记录操作者、时间、对象、结果;
- 密钥统一走根目录 `.env`(pydantic-settings 读取),代码/仓库禁止硬编码。

## 四、改进路线图(按优先级,未执行)

### R1 安全基线(上线前)
- frp token 轮换 + 移入 `.env` 插值;删除仓库中的真实 token
- JWT + 角色鉴权中间件 + admin 操作审计日志;CORS 白名单改为配置注入
- Dify 回调共享密钥(Header 校验)
- 默认凭据拒绝:database_url / redis_url / Dify 密钥默认值改为必填(空则启动失败)
- 微信 POST 回调补齐验签 + 解密 + 入队;网关层风险关键词护栏 + 限流

### R2 异步可靠性
- 引入 Celery(设计已定 W3,提前落地)+ 任务重试/死信/超时
- `sync_approved_knowledge` 从 BackgroundTasks 迁移到 Celery;知识同步状态机(`sync_status / sync_attempts / sync_error`)字段 + 重试管理端点
- 驳回/删除知识项时同步删除 Dify 文档(delete 接口);提供对账脚本
- `pool_pre_ping` + 池大小配置;httpx 客户端复用(模块级 Client)
- 负面情绪计数接线(route_incoming 内实现连续负面检测)

### R3 会话/知识闭环
- 知识驳回/删除同步(与 R2 合并落地);日志湖按月分区 + 归档策略
- HITL 通知(企微通知人工)+ 接管页面 + 满意度回传交互

### R4 可观测性与 CI
- Prometheus metrics(请求量/延迟/Dify 耗时/token 消耗/队列深度/错误率)+ 集中日志 + 告警规则
- CI(gitleaks 密钥扫描 + pytest + ruff/flake8 + alembic 迁移检查 + docker build);依赖锁定(uv/pip-tools + digest)
- 测试隔离(Testcontainers 或 docker compose 测试栈)

### R5 多代理架构演进(为 sales/doctor 铺路)
- 共享 Agent Runtime 层:渠道适配(ChannelAdapter)、RAG/Dify 调用、HITL、日志、未解答捕获
- 消除 core 三表循环外键(改逻辑关联);统一 match_score 字段类型
- Dify workflow DSL 版本化(导入/导出 + code review);workflow 回调带共享密钥
- 会话 ID 规范(群维度 session)、H5 身份体系设计

---

## 五、计划内事项(非缺陷,按 6 周排期落地)

- 微信回调 POST 验签/解密/主动推送 → W3(Task 3.1/3.2)
- Celery 异步队列 → W3(Task 3.2)
- Dify 意图路由/降级/风控编排 → W4(Task 4.x)
- 全链路日志落库 → W5(Task 5.1)
- 攻防测试、灰度、稳定性验收 → W6(Task 6.x)

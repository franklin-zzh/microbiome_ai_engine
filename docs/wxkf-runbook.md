# WXKF 微信客服接入联调手册（wxkf-runbook）

> 企微「微信客服」接入的**配置 + 联调操作手册**：从企微后台配置、frp 公网链路、本地启动，
> 到三态（NORMAL / BLOCKED / HUMAN_MODE）端到端验证与故障排查，按顺序照做即可。
>
> 关联文档：`docs/CS-WORKFLOW-BASELINE.md`（Dify 工作流冒烟基线）、`docs/CS-ISSUE-LOG.md`（问题记录表）、
> `docs/DIFY-API-CONTRACT.md`（Dify API 契约）、`frp-client/README.md`（frp 隧道搭建）。

---

## 1. 当前状态盘点（截至 2026-08-14）

| 项 | 状态 | 说明 |
|---|---|---|
| 回调路由（GET 校验 + POST 验签解密） | ✅ 代码完成 | `backend/app/agent_cs/router.py`，挂载于根前缀 `/wx/msg` |
| 加解密工具 | ✅ 代码完成 | `backend/app/clients/wx/wxbizmsgcrypt.py`（官方 WXBizMsgCrypt） |
| 主动推送/拉取客户端 | ✅ 代码完成 | `backend/app/clients/wx/wxkf_client.py`：gettoken + `kf/send_msg` + `kf/sync_msg` |
| Dify 对话客户端 | ✅ 代码完成 | `backend/app/clients/dify_chat_client.py`：`chat-messages` blocking |
| 状态机分流 | ✅ 代码完成 | `backend/app/agent_cs/services.py`：NORMAL / HUMAN_MODE / BLOCKED |
| 日志湖落库 | ✅ 代码完成 | `cs_chat_logs`（channel=WXKF），含 RAG 召回切片与 meta |
| 自动化测试 | ✅ 51 个通过 | `backend/tests/`（含 `test_wxkf_client.py`、`test_wechat_reply.py`） |
| `.env` 企微密钥 | ✅ 已填 | `WXKF_CORP_ID / WXKF_SECRET / WXKF_TOKEN / WXKF_ENCODING_AES_KEY` |
| frp 隧道 | ✅ 已打通 | frps token 已同步；`https://wx.fmtcloud.cn` 可访问本地 backend |
| E7 回答泄漏 `<think>` | ✅ 已修复 | Dify 控制台已加剥离处理，不再输出思考内容 |
| 企微后台回调配置 | ⏳ 待操作 | 见 §4（需企微管理员权限） |
| 三态端到端实测 | ⏳ 待联调 | 见 §6（需真实微信消息） |

---

## 2. 链路架构

### 2.1 数据流总览

```
微信用户（个人微信）
   │  ① 用户在微信里找到企业「微信客服」入口并发消息
   ▼
企微微信客服（企业微信服务端）
   │  ② 回调推送：https://wx.fmtcloud.cn/wx/msg（POST，XML 密文，须 5 秒内回 success）
   ▼
云服务器 Nginx（wx.fmtcloud.cn:443，HTTPS）
   │  ③ 反向代理到 127.0.0.1:6001
   ▼
frps（云服务器 :6001 端口）
   │  ④ 内网穿透隧道（token 认证）
   ▼
frpc（本地开发机，frp-client/frpc.toml）
   │  ⑤ 转发到 host.docker.internal:8000
   ▼
FastAPI backend（本地 :8000）
   │  ⑥ GET /wx/msg：URL 校验（decrypt_echo_str 回明文 echostr）
   │  ⑦ POST /wx/msg：验签 + 解密 → Redis 状态机分流 → 立即回 "success"（<5s）
   │  ⑧ 后台异步任务（asyncio.create_task，线程池执行）：
   ▼
Redis db3 ──会话状态 / conversation_id / access_token 缓存
MySQL mb_ai_engine ──cs_chat_logs 落库（日志湖）
Dify（192.168.110.16:3080）── POST /v1/chat-messages（blocking）拿回答
企微 API（qyapi.weixin.qq.com）── cgi-bin/kf/send_msg 主动推送回复给用户
```

### 2.2 双层风控（医疗合规红线）

| 层 | 位置 | 机制 | 成本 |
|---|---|---|---|
| 第一道 | backend 状态机（`services.py route_incoming`） | 命中 `RISK_KEYWORDS` → 直接 `BLOCKED`，回预设安全话术 | **0 LLM 调用** |
| 第二道 | Dify 工作流 `risk_guard` 节点（LLM 语义兜底） | 关键词漏网的高风险语义再拦截 → `risk_handoff` | deepseek-v4-flash + max_tokens 256 |

### 2.3 5 秒异步回包硬约束

- 微信/企微回调要求 **5 秒内返回 `success`**，否则重试 / 报错。
- 回调接口内**严禁同步调 Dify**：状态机分流与落库均为毫秒级，先回 `success`，AI 逻辑全部放进 `asyncio.create_task(_handle_wechat_reply(...))` 后台执行（同步网络/DB 调用经 `asyncio.to_thread` 放入线程池，不阻塞事件循环）。

---

## 3. 代码现状与关键入口

### 3.1 模块清单

| 文件 | 职责 |
|---|---|
| `backend/app/agent_cs/router.py` | 微信回调路由（`/wx/msg` GET 校验 + POST 消息）、后台回复链路、`cs_chat_logs` 落库 |
| `backend/app/agent_cs/services.py` | Redis 会话状态机：`get_state / set_state / mark_human / mark_blocked / reset`、`route_incoming` 分流、关键词表 |
| `backend/app/clients/wx/wxbizmsgcrypt.py` | 企微官方加解密：`decrypt_echo_str`（GET 校验）、`decrypt_msg`（POST 验签+解密） |
| `backend/app/clients/wx/wxkf_client.py` | `get_access_token`（Redis 缓存 7000s，官方 7200s 提前刷新；errcode 40014/42001/4502 失效自动重试）、`send_kf_text(open_kf_id, touser, content)` → `POST cgi-bin/kf/send_msg`、`sync_msg(open_kfid, token, cursor)` → `POST cgi-bin/kf/sync_msg`（拉取消息） |
| `backend/app/clients/dify_chat_client.py` | `chat_messages(query, user, conversation_id)` → `POST /chat-messages`（blocking），`trust_env=False` 防代理 502；key 取 `DIFY_CHAT_API_KEY`，留空回退 `DIFY_API_KEY` |
| `backend/main.py` | 挂载：`app.include_router(wechat_router, prefix="")` → 回调 URL `https://wx.fmtcloud.cn/wx/msg` |
| `backend/tests/test_wxkf_client.py` | access_token 缓存 / 主动推送 / token 失效重试 |
| `backend/tests/test_wechat_reply.py` | 回调验签 / 状态机三态 / 推送与落库 |

### 3.2 回调路由行为细节

- **GET `/wx/msg`**（企微后台点「保存」时触发的 URL 校验）：参数 `msg_signature / timestamp / nonce / echostr` → `decrypt_echo_str` → 直接返回解密后的**明文 echostr**（`text/plain`）；失败回 400。
- **POST `/wx/msg`**（真实消息回调）：参数同上，body 为企微标准 XML（含 `<Encrypt>`）：
  - 空 body → 400；body > 1MB → 413；XML 解析失败 → 400；缺 `<Encrypt>` → 400；
  - 验签/解密失败 → 400（事件 `wechat_kf_msg_verify_failed`）；
  - 解密后取字段：`Event`（事件类型，如 `kf_msg_received` / `enter_session` / `kf_msg_or_event`）、`MsgType2`（消息真实类型，仅 kf_msg_received 存在）、`ExternalUserID`（**用户外部联系人 ID**，推送的 touser；微信客服回调**没有** `FromUserName` 字段）、`OpenKfId`（**客服账号 open_kfid**，推送的 open_kf_id；`ToUserName` 是 corp_id 不能用于推送）、`Token`、`Content`、`MsgId`；
  - **`kf_msg_or_event`**（企微只通知「有新消息/事件」，不携带消息体，`from_user`/`content` 均为空）：用回调里的 `Token` + `OpenKfId` 调 `sync_msg` 主动拉取消息，再逐条走状态机 + 回复（`_sync_and_reply_async`）；
  - 非文本消息 → 回 `NON_TEXT_REPLY` 提示，不进入状态机（事件 `wechat_kf_msg_ignored`）；
  - 会话标识：`session_id = "wxkf:{from_user}"`。
  - **即时 ack**：需经由 Dify 深度思考的长链路问题先推「收到，正在为您查询，请稍候～」（`.env WXKF_ACK_REPLY` 可覆盖），再异步等 Dify 正式回答——用户感知等待从 ~12s 降到 ~1.5s；纯日常问候与菜单指令自动跳过 ack 直发回答。
  - **日常问候与菜单 Fast-Path 极速秒回**：用户发送纯问候（“你好/您好/在吗/hi/早上好”）或菜单指令（“菜单/常见问题/help”）时，后端识别后直接调用 `send_kf_menu` 发送富玛特小助手欢迎语 + 3 个快捷问题按钮，**耗时 <50ms，零 LLM 成本，不调用 Dify**。
  - **enter_session 欢迎语 + 快捷菜单**：用户进入会话时用事件回调携带的 `WelcomeCode` 调 `send_msg_on_event` 推**单条 msgmenu**（欢迎语文本 + 三个快捷提问按钮合并：检测流程/报告解读/调理服务）；**无 `WelcomeCode`（48h 内已欢迎过或用户已发过消息）直接跳过**，不再降级 `send_msg`（企微规则：用户未发消息时 `send_msg` 主动下发必 95001）；点击菜单项后企微自动以文本消息回复对应问题（带 `text.menu_id`），走正常 sync_msg 问答链路；不进入状态机；避免使用企微后台欢迎语（其「知识库问答」选项会开启企微机器人接管消息）。

### 3.3 会话状态机（Redis db3）

| Key 格式 | 内容 |
|---|---|
| `session:{session_id}:state` | `NORMAL / HUMAN_MODE / BLOCKED`，TTL = `SESSION_TTL_SECONDS`（1800s） |
| `session:{session_id}:neg_streak` | 连续负面情绪计数（预留，当前未接线，见 AGENT.md 待办 #9） |
| `session:{session_id}:context` | 上下文快照（预留） |
| `session:{session_id}:dify_conv` | Dify `conversation_id`（多轮续聊） |
| `wx:kf:access_token` | 企微 access_token 缓存（7000s） |

`route_incoming` 分流优先级（**风险词 > 转人工诉求 > 正常回答**）：

1. 当前已是 `BLOCKED` → 保持，`should_answer=False`；
2. 命中 `RISK_KEYWORDS` → `mark_blocked`，`hit_keyword` 记录命中词（**HUMAN_MODE 下也升级为 BLOCKED**）；
3. 命中 `HUMAN_REQUEST_KEYWORDS` 或已是 `HUMAN_MODE` → `mark_human`，AI 停止抢答；
4. 否则 `NORMAL`，`should_answer=True`。

**触发词表（代码内维护，改后重启生效）：**

```python
# services.py
HUMAN_REQUEST_KEYWORDS = ("转人工", "人工客服", "找人工", "真人", "客服电话", "投诉")
RISK_KEYWORDS = (
    "便血", "黑便", "鲜血便", "柏油样便", "大便带血", "呕血",
    "剧烈腹痛", "持续腹痛", "绞痛", "肚子疼到打滚", "无法直立",
    "持续腹泻", "水样便", "频繁呕吐", "脓血便", "体重骤降", "不明原因消瘦",
    "肠梗阻", "肠穿孔", "肠癌", "直肠癌", "结肠癌", "高烧不退", "严重脱水",
)
GREETING_KEYWORDS = (
    "你好", "您好", "hi", "hello", "在吗", "在嘛", "在不", "早", "早上好",
    "中午好", "下午好", "晚上好", "嗨", "哈喽", "hey",
)
MENU_KEYWORDS = (
    "菜单", "快捷菜单", "常见问题", "menu", "help", "帮助", "功能", "指引", "导航",
)
```

### 3.4 预设话术（router.py，0 LLM 成本）

| 场景 | 话术 | 说明 |
|---|---|---|
| 风险拦截 BLOCKED | `您描述的情况可能涉及健康风险，我这边无法在线判断。建议您尽快联系专业医生或拨打客服热线。我马上为您转接专属健康顾问。` | `.env RISK_BLOCKED_REPLY` 可覆盖；与工作流 risk_handoff 话术一致 |
| 转人工 HUMAN_MODE | `正在为您转接人工客服，请稍候。` | `HUMAN_HANDOFF_REPLY` |
| 日常问候 / 菜单 Fast-Path | 欢迎语 + 3 个快捷问题交互菜单（检测流程/报告解读/调理服务） | `_DEFAULT_WXKF_WELCOME_REPLY` + `WXKF_MENU_ITEMS`，落库 `meta.route="greeting_fast_path"` 或 `"menu"` |
| Dify 调用失败兜底 | `抱歉，系统暂时繁忙，请稍后再试。` | `DIFY_FAILBACK_REPLY`，落库 `meta.route="dify_error"` |
| 非文本消息 | `您好，我暂时只能处理文字消息，请用文字描述您的问题～` | `NON_TEXT_REPLY` |
| 即时 ack | `收到，正在为您查询，请稍候～` | `.env WXKF_ACK_REPLY` 可覆盖 |
| 欢迎语（进入会话） | `您好，我是「肠道健康」品牌的智能客服助手，可以为您解答肠菌检测流程、报告解读（非诊断）、肠道健康科普以及产品与调养服务相关问题。请问有什么可以帮您？` | `.env WXKF_WELCOME_REPLY` 可覆盖；走 `send_msg_on_event`（`WelcomeCode`）推单条 msgmenu（欢迎语+三个按钮）；无 code 跳过 |

### 3.5 `.env` 配置项

```dotenv
# ---- 微信客服 (企微 API) ----
WXKF_CORP_ID=wwd5f2644ae2a6a894        # 企业 ID（企微后台 → 我的企业）
WXKF_SECRET=...                        # 「微信客服 → API」页生成的 Secret
WXKF_TOKEN=...                         # 回调配置页生成/自定义的 Token
WXKF_ENCODING_AES_KEY=...              # 回调配置页生成的 43 位 EncodingAESKey

# ---- Dify（客服对话）----
DIFY_BASE_URL=http://192.168.110.16:3080/v1
DIFY_API_KEY=app-...                   # 客服应用 API Key（DIFY_CHAT_API_KEY 留空时回退用）
DIFY_CHAT_API_KEY=                     # 客服应用专用对话 Key（推荐单独建；留空回退 DIFY_API_KEY）

# ---- 风控 ----
RISK_BLOCKED_REPLY=                    # 命中高风险关键词的预设话术（留空用内置默认）

# ---- 微信客服话术 ----
WXKF_ACK_REPLY=                        # 收到消息的即时 ack（留空用内置默认）
WXKF_WELCOME_REPLY=                    # 进入会话的欢迎语（留空用内置默认）
```

> ⚠️ `WXKF_TOKEN` 与 `WXKF_ENCODING_AES_KEY` 必须与企微后台回调配置页**逐字符完全一致**（包括大小写与位数），否则 GET 校验 / POST 验签必失败。

---

## 4. 企微后台配置（需企微管理员，手动操作）

> 入口：企业微信管理后台 → 「客户与上下游」/「微信客服」（或直接搜索「微信客服」应用）。

### 4.1 确认企业 ID（CorpID）

- 管理后台 → 「我的企业」→「企业信息」→ **企业ID**。
- 核对 `.env` 的 `WXKF_CORP_ID` 一致（当前 `wwd5f2644ae2a6a894`）。

### 4.2 获取微信客服 Secret

- 管理后台 → 「微信客服」→「API」→ 生成/查看 **Secret**。
- 填入 `.env` 的 `WXKF_SECRET`。
- 该 Secret 用于 `gettoken` 换取 access_token（`POST https://qyapi.weixin.qq.com/cgi-bin/gettoken`）。

### 4.3 生成回调 Token 与 EncodingAESKey

- 「微信客服」→「API」→「接收消息」设置页：
  - **Token**：随机生成（建议 16 位以上字母数字）；
  - **EncodingAESKey**：页面点击「随机生成」，固定 **43 位**；
- 把两者填入 `.env` 的 `WXKF_TOKEN` / `WXKF_ENCODING_AES_KEY`（**逐字符一致**）。

### 4.4 配置回调 URL

- 回调 URL 填：`https://wx.fmtcloud.cn/wx/msg`
- 保存时企微会发 **GET 校验请求**（`msg_signature/timestamp/nonce/echostr` 四参数），
  后端 `verify_wechat_url` 验签后返回明文 echostr；通过即保存成功。
- 校验失败的排查见 §7（`wechat_kf_verify_error` 事件）。
- ⚠️ 保存前确认：① 本地 backend 已启动（§5.1）；② frpc 已启动且隧道通（§5.3）；否则企微连不上 `wx.fmtcloud.cn`。

### 4.5 创建客服账号（open_kfid）

- 「微信客服」→「客服账号」→ 新建客服账号（如「AI 健康顾问」）。
- 记下该账号的 **open_kfid**（联调时用于确认是哪个账号收到消息）。
- **无需回填到 `.env`**：用户给该账号发消息时，回调消息里的 `OpenKfId` 就是 open_kfid，
  后端直接用它做 `kf/send_msg` 主动推送（`router.py` 第 448 行 `open_kf_id=open_kf_id`）。
- 客服账号需在「接待人员」中至少添加一名成员，且企业微信需开启「微信客服」应用权限。

### 4.6 配置核对表

| 项 | 值 | 是否一致 |
|---|---|---|
| CorpID | `.env WXKF_CORP_ID` == 后台「我的企业」 | ☐ |
| Secret | `.env WXKF_SECRET` == 后台「微信客服 → API」 | ☐ |
| Token | `.env WXKF_TOKEN` == 后台回调设置（逐字符） | ☐ |
| EncodingAESKey | `.env WXKF_ENCODING_AES_KEY` == 后台回调设置（43 位逐字符） | ☐ |
| 回调 URL | 后台 == `https://wx.fmtcloud.cn/wx/msg` | ☐ |
| 客服账号 | 已创建，`open_kfid` 已知 | ☐ |

---

## 5. 本地启动与公网预检

### 5.1 启动 backend

```bat
:: 一键启动（自动使用 .venv，uvicorn --reload，0.0.0.0:8000）
backend\dev.bat
```

或手动：

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

启动后自检：

- `http://localhost:8000/health` → `{"status":"ok","redis":"ok"}`（Redis 连不上会非 ok）
- `http://localhost:8000/docs` → Swagger 可见 `/wx/msg` 两个端点

### 5.2 回归测试

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/ -v
```

预期：全部通过（51 个，含 wxkf 验签/推送/拉取/落库用例）。提交流程前必须全绿。

### 5.3 frp 隧道预检

本地 frpc 配置（`frp-client/frpc.toml`，真实 token 不入库）：

```toml
serverAddr = "39.97.252.180"
serverPort = 7001
auth.token = "0ad1fc880ac3e17515dc3856c0974104da3e966365f6d154"  # 已与服务器 frps.toml 同步
[[proxies]]
name = "wechat-kf"
type = "tcp"
localIP = "host.docker.internal"
localPort = 8000
remotePort = 6001
```

启动 frpc（按 `frp-client/README.md`，docker compose 或二进制）后验证：

```powershell
# 外网链路探活（应返回本地 backend 的内容）
curl https://wx.fmtcloud.cn/health
curl https://wx.fmtcloud.cn/wx/msg          # 无参数应 422（FastAPI 校验），说明链路通
curl "https://wx.fmtcloud.cn/wx/msg?msg_signature=x&timestamp=1&nonce=x&echostr=x"  # 应 400 验签失败而非超时
```

> 三条 curl 的判定：能拿到 FastAPI 的 JSON / 422 / 400 响应即**隧道通**；超时 / 502 则 frp 或 nginx 有问题（见 §7.2）。

---

## 6. 三态联调验证（端到端）

> 前置：backend 已启动、frpc 已启动、企微后台回调已配置（§4）、客服账号可被微信用户访问。
> 验证方式：真实微信 → 企业微信客服入口 → 给客服账号发消息；同时盯 backend 控制台结构化日志与 MySQL 落库。

### 6.1 通用观察手段

**日志**：backend 控制台（结构化 JSON，logger `agent_cs`）。核心事件全表：

| 事件 | 含义 |
|---|---|
| `wechat_kf_verify_success` | GET URL 校验通过（回明文 echostr） |
| `wechat_kf_verify_error` / `wechat_kf_verify_failed` | GET 校验失败 / 未配置密钥 |
| `wechat_kf_msg_received` | POST 消息接收（记录 from_user=ExternalUserID / msg_type / wechat_event / content_len / msg_id，**不存原文**） |
| `wechat_kf_sync_notified` | 收到 `kf_msg_or_event` 通知（只带 open_kfid/token，即将触发 sync_msg 拉取） |
| `wechat_kf_welcome` | enter_session 事件：解析 WelcomeCode 并调度欢迎语推送（含 open_kfid/from_user/has_code） |
| `wxkf_welcome_skipped` | 无 WelcomeCode（48h 内已欢迎过或用户已发过消息）：跳过，不推送 |
| `wxkf_welcome_on_event_sent` / `wxkf_welcome_on_event_failed` | send_msg_on_event 欢迎语推送成功（含 msgid） / 失败（errcode/errmsg） |
| `wxkf_sync_failed` / `wxkf_sync_skipped` | sync_msg 拉取失败 / 无 open_kfid 跳过 |
| `wechat_kf_sync_task_error` | sync 拉取后台任务异常 |
| `wechat_kf_msg_ignored` | 非文本消息（回 NON_TEXT_REPLY） |
| `wechat_kf_route` | 状态机分流结果（state / hit_keyword / should_answer） |
| `wxkf_send_msg` | 主动推送成功（含 msgid） |
| `wxkf_send_menu` | msgmenu 菜单消息推送成功（含 items 与 msgid） |
| `wxkf_push_skipped` | 无 open_kf_id 跳过推送 |
| `wxkf_push_failed` | 推送失败（errcode/errmsg） |
| `dify_chat_failed` | Dify 调用失败（走兜底话术） |
| `cs_chat_log_written` | 日志湖落库成功（含 item_id / session_id / channel / hit_human） |
| `wechat_kf_reply_failed` / `wechat_kf_reply_task_error` | 后台回复链路异常 |

**落库**：`mb_ai_engine.cs_chat_logs` 关键字段：

| 字段 | 说明 |
|---|---|
| `channel` | `WXKF` |
| `session_id` | `wxkf:{from_user}` |
| `open_id` | 用户外部联系人 ID（ExternalUserID） |
| `user_message` / `ai_reply` | 原文 / 回复 |
| `hit_human` | BLOCKED 与 HUMAN_MODE 均为 `true` |
| `risk_flag` | 仅 BLOCKED 为 `true` |
| `retrieved_chunks` | Dify RAG 召回切片（NORMAL 态） |
| `meta` | `{"route": "dify"|"blocked"|"human"|"dify_error", "hit_keyword": ..., "conversation_id": ...}` |
| `created_at` | 落库时间 |

查库示例：

```sql
SELECT id, session_id, open_id, hit_human, risk_flag, meta
FROM cs_chat_logs
ORDER BY id DESC LIMIT 10;
```

### 6.2 三态验证步骤

#### ① NORMAL（正常问答）

1. 微信里给客服账号发一条正常问题，如「你们检测肠道菌群要多少钱」；
2. 微信在 **5 秒内**收到回复（AI 生成后主动推送）；
3. 日志顺序应出现：`wechat_kf_msg_received` → `wechat_kf_route(state=NORMAL, should_answer=true)` → `wxkf_send_msg` → `cs_chat_log_written`；
4. `cs_chat_logs` 新增记录：`channel=WXKF`、`hit_human=false`、`risk_flag=false`、`meta.route="dify"`、`retrieved_chunks` 有召回；
5. 追问一句（多轮）：Redis 里 `session:{wxkf:xxx}:dify_conv` 有值，第二轮回调携带同一 conversation_id 续聊（NORMAL 态重复 ①② 即可验证）。

#### ② BLOCKED（高风险关键词拦截）

1. 发「我最近**便血**了怎么办」；
2. 应立即收到**安全话术**（§3.4，`RISK_BLOCKED_REPLY` 未配则用内置默认）；
3. 日志：`wechat_kf_route(state=BLOCKED, hit_keyword="便血", should_answer=false)` → `wxkf_send_msg` → `cs_chat_log_written`；
4. **确认 0 LLM 调用**：日志中无 `dify_chat_failed` 与 Dify 相关事件，Dify 控制台无新会话；
5. `cs_chat_logs`：`risk_flag=true`、`hit_human=true`、`meta.route="blocked"`、`meta.hit_keyword="便血"`；
6. 状态机已锁 `BLOCKED`：此时再发普通问题，仍应回安全话术（`route_incoming` 第 1 分支）。

#### ③ HUMAN_MODE（转人工）

1. 发「我要**转人工**」；
2. 应立即收到「正在为您转接人工客服，请稍候。」；
3. 日志：`wechat_kf_route(state=HUMAN_MODE, hit_keyword="转人工")` → `cs_chat_log_written`；
4. `cs_chat_logs`：`hit_human=true`、`risk_flag=false`、`meta.route="human"`；
5. 后续再发问题：AI **不再抢答**，持续回转人工话术（HUMAN_MODE 保持）；
6. 管理员在管理后台/接口重置状态为 NORMAL 后恢复自动回答（`reset`，Redis `session:{sid}:state` 置 NORMAL）。

### 6.3 边界场景抽查

| 场景 | 操作 | 预期 |
|---|---|---|
| 非文本消息 | 发图片 / 语音 | 回「我暂时只能处理文字消息…」；日志 `wechat_kf_msg_ignored`；不入状态机 |
| Dify 不可用 | 临时停 Dify 后发消息 | 回「系统暂时繁忙」；日志 `dify_chat_failed`；落库 `meta.route="dify_error"` |
| 高频问题 | 连发 5-10 条正常问题 | 每轮回调均 5 秒内回 success，无 5 秒超时报警 |

### 6.4 端到端延迟构成（实测，2026-08-17）

| 段 | 耗时 | 说明 |
|---|---|---|
| 微信 → 回调 + sync_msg 拉取 | ~0.5s | `kf_msg_or_event` 通知后拉取 |
| Dify blocking 响应 | **7-13s（大头）** | LLM 推理 + 工作流节点串行；换快模型/精简节点可降到 1-3s |
| `kf/send_msg` 推送 | ~0.6s | 企微 API 响应 |
| 微信客户端下发 | ~1-2s | 微信侧不可控 |

优化：后端已加即时 ack（§3.2，感知等待已缩短）；**治本在 Dify 侧**——控制台把 LLM 节点换非 reasoning 模型（如 deepseek-chat/gpt-4o-mini/qwen-turbo）、减少串行 LLM 节点数、确认 Dify 服务器 CPU/内存充足。

### 6.5 快捷提问菜单（msgmenu）验证

1. 新用户（48h 内未进过会话）进入会话：应收到**单条**带三个按钮的菜单消息（欢迎语文本合并其中；检测流程/报告解读/调理服务），日志 `wxkf_welcome_on_event_sent`；
2. 同一用户结束会话后再次进入（48h 内）：日志出现 `wxkf_welcome_skipped`，微信端**不再**收到欢迎语（企微规则：同一用户 48h 只欢迎一次），且不再出现 95001 `wxkf_push_failed`；
3. 点击任一按钮 → 微信端自动发出该问题文本 → 后端正常回答（日志 `wechat_kf_route` 出现 `menu_id=q_flow` 等标记）；
4. 菜单项数限制：click/view/miniprogram 合计 ≤10 个；msgmenu `head_content`/`tail_content` ≤1024 字节；**文本消息不支持 Markdown**（`**加粗**` 会原样显示，需用「」/换行排版）。

---

## 7. 故障排查表

| # | 现象 | 可能原因 | 排查动作 |
|---|---|---|---|
| 1 | 企微后台保存回调 URL 失败 | Token / EncodingAESKey 与 `.env` 不一致；backend 未启动；frp 隧道断 | 核对 §4.6；确认本地 8000 起；`curl https://wx.fmtcloud.cn/health`；看日志 `wechat_kf_verify_error` |
| 2 | `wechat_kf_verify_failed`（500） | `.env` 缺 `WXKF_TOKEN` / `WXKF_ENCODING_AES_KEY` | 补全后重启 backend |
| 3 | POST 消息一直 400 `wechat_kf_msg_verify_failed` | Token / EncodingAESKey / CorpID 与后台不一致（多为 EncodingAESKey 位数或大小写） | 逐字符核对 §4.6；重启 backend 使新配置生效 |
| 4 | 微信收不到回复 | ① `wxkf_push_skipped`：回调里 `OpenKfId` 为空 → 客服账号问题；② `wxkf_push_failed`：access_token 失效或 Secret 错、或 `40058 missing field open_kfid`（send_msg body 漏 open_kfid，曾为 bug 已修，勿回退）；③ Dify 慢/挂 | 看对应事件日志；`gettoken` 手动调一次核对 Secret；确认 Dify 控制台会话在跑 |
| 5 | 回复内容带 `<think>...</think>` | Dify 工作流 answer_llm 未剥离思考内容（E7 复发） | Dify 控制台检查剥离节点，`smoke_chat.py` 复测 |
| 6 | 回包超 5 秒 | Dify 阻塞（正常分支基线 10.1s，属正常范围——后台异步不受影响）；Redis/MySQL 慢 | 看 `cs_chat_log_written` 时间与推送时间差；正常分支慢见 CS-ISSUE-LOG S3 观察项 |
| 7 | `curl https://wx.fmtcloud.cn` 超时 / 502 | frpc 未启动；frps token 不一致被拒；nginx 未 reload；本地 backend 未起 | 本地起 frpc 看日志；服务器 `docker logs frps`；`curl http://127.0.0.1:6001`（服务器侧）；`systemctl reload nginx` |
| 8 | Dify 返回 401 / 404 | key 前缀不对：对话要用 app- 前缀（`DIFY_CHAT_API_KEY` 或回退 `DIFY_API_KEY`），知识库才是 dataset- 前缀 | 确认 `.env`；`dify_chat_client` 日志；`docs/DIFY-API-CONTRACT.md` |
| 9 | `cs_chat_logs` 无新记录 | MySQL 未起 / Alembic 未迁移 / `alembic upgrade head` 未执行 | `docker compose logs backend`；`cd backend && .venv\Scripts\python.exe -m alembic upgrade head` |
| 10 | 收到 `wechat_kf_msg_received` 但 `wechat_event=kf_msg_or_event`、`content_len=0`，或日志只有 `wechat_kf_sync_notified` 却无回复 | 客服账号走「事件通知 + sync_msg 拉取」模式：回调只带 `Token`+`OpenKfId` 不带消息体（不是未勾选消息类型），需后端调 `sync_msg` 拉取（已实现于 `router.py _sync_and_reply_async`） | 确认 backend 已重启加载新代码；重发消息，日志应出现 `wechat_kf_sync_notified` → `wechat_kf_route(source=sync_msg)` → `wxkf_send_msg` → `cs_chat_log_written`；若出现 `wxkf_sync_failed` 看 errcode/errmsg（多为 access_token/Secret 或回调 Token 过期） |
| 11 | 中文日志乱码 | PowerShell GBK 控制台 | 运行前 `chcp 65001` 或 `$env:PYTHONIOENCODING="utf-8"` |

---

## 8. 收尾核对清单

- [ ] 企微后台：回调 URL / Token / EncodingAESKey 与 `.env` 一致（§4.6 全勾）
- [ ] 客服账号已创建，微信侧可进入会话
- [ ] `pytest tests/ -v` 全绿（51 个）
- [ ] `curl https://wx.fmtcloud.cn/health` 返回 backend 内容（frp 链路通）
- [ ] NORMAL 态：微信收到 AI 回复，`cs_chat_logs` 有 `meta.route="dify"` 记录，多轮续聊正常
- [ ] BLOCKED 态：发「便血」回安全话术，0 LLM 调用，`risk_flag=true`
- [ ] HUMAN_MODE 态：发「转人工」回转接话术，后续 AI 不抢答
- [ ] 非文本消息回提示；Dify 停用回兜底话术（抽查项）
- [ ] 每轮回调均 5 秒内回 `success`
- [ ] 更新 `AGENT.md` 动态记忆区（勾待办 #6/#8，记录联调结果）

---

## 9. 后续规划（不阻塞上线）

- **Celery 异步队列**：当前用进程内 `asyncio.create_task` 顶住；消息量上来或需多 worker 时引入 Celery（AGENT.md 待办 #8 后半）。
- **负面情绪接线**：`increment_negative_streak` 目前是死代码（AGENT.md 待办 #9），「连续负面 ≥ 2 转人工」规则未启用，需要时接线。
- **渠道扩展**：公众号/服务号（`MP`，推送走 `cgi-bin/message/custom/send`）、H5 聊天窗（`H5`）、企微群机器人（`WECOM_GROUP`）——`cs_chat_logs.channel` 枚举已预留。

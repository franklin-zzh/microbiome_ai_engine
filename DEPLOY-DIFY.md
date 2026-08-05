# Dify 局域网服务器部署手册（192.168.110.16）

目标:把 Dify 1.16.1 部署到局域网服务器,对外只暴露 **web 控制台 `3080`** 与 **API `5081`** 两个端口,
数据卷统一落在 `/data/data2025/fmt_software/fmt-infra/dify/docker/volumes`(磁盘空间充足分区)。
backend(FastAPI)继续留在本地开发机,通过局域网连服务器 Dify。

---

## 1. 文件清单

从仓库取这两个文件(已改造完成,勿再用旧版):

| 文件 | 说明 |
|---|---|
| `docker-compose.dify.yml` | 端口统一、数据路径变量化后的 compose |
| `.env.dify.example` | 环境变量模板(复制为 `.env.dify` 使用) |

## 2. 服务器前置检查

```bash
# 1) 确认 docker 与 compose v2
docker -v
docker compose version          # 无则用 docker-compose

# 2) 确认 /data 分区空间充足(Weaviate+PG+插件数据预计 5-20GB)
df -h /data

# 3) 确认 3080 / 5081 未被占用
ss -ltn | grep -E ':(3080|5081)'
```

## 3. 建目录与数据卷目录

```bash
BASE=/data/data2025/fmt_software/fmt-infra/dify
mkdir -p "$BASE/docker/volumes"/{db/data,redis,weaviate,sandbox/{dependencies,conf},plugin_daemon,app/storage}
```

## 4. 生成 .env.dify

```bash
cd "$BASE"
cp .env.dify.example .env.dify

# 生成并替换 SECRET_KEY(必须)
NEW_KEY=$(openssl rand -base64 42)
sed -i "s|SECRET_KEY=.*|SECRET_KEY=$NEW_KEY|" .env.dify

# 建议同时修改:REDIS_PASSWORD / DB_PASSWORD / WEAVIATE_API_KEY /
# SANDBOX_API_KEY / PLUGIN_DAEMON_KEY / PLUGIN_DIFY_INNER_API_KEY /
# DIFY_AGENT_SERVER_SECRET_KEY / DIFY_AGENT_API_TOKEN(openssl rand 或手动)
```

确认 `.env.dify` 中关键项:

```bash
grep -E 'SERVER_IP|WEB_PORT|API_PORT|DIFY_DATA_ROOT|SECRET_KEY' .env.dify
# 期望:
#   SERVER_IP=192.168.110.16
#   WEB_PORT=3080
#   API_PORT=5081
#   DIFY_DATA_ROOT=/data/data2025/fmt_software/fmt-infra/dify/docker/volumes
```

## 5. 上传文件(本地开发机执行)

```bash
# 在本地仓库根目录执行;把 <用户> 换成服务器登录用户名
scp -i ~/.ssh/<你的密钥> docker-compose.dify.yml .env.dify.example \
    <用户>@192.168.110.16:/data/data2025/fmt_software/fmt-infra/dify/
```

## 6. 启动

```bash
cd /data/data2025/fmt_software/fmt-infra/dify
docker compose -f docker-compose.dify.yml --env-file .env.dify up -d

# 观察启动进度(首次拉镜像约 10-20 分钟)
docker compose -f docker-compose.dify.yml logs -f --tail 50
```

## 7. 验证

```bash
# 服务器本机
curl -s http://127.0.0.1:5081/health          # API 健康检查
curl -sI http://127.0.0.1:3080 | head -1       # 控制台返回 200

# 数据落在 /data 分区
df -h /data

# 容器状态全部 running/healthy(等 1-2 分钟)
docker compose -f docker-compose.dify.yml ps

# 局域网其他机器(本地电脑)浏览器访问
#   控制台: http://192.168.110.16:3080   (首次进入需注册管理员账号)
```

> 提示:Dify 控制台首次打开需创建管理员账号,然后进入"设置 → 模型供应商"配置 LLM/Embedding 的 API Key。

---

## 8. 本地 ↔ 服务器连通(backend 留在本地开发机)

### 8.1 本地 .env 指向服务器 Dify

本地仓库根目录 `.env` 修改:

```ini
DIFY_BASE_URL=http://192.168.110.16:5081/v1
DIFY_API_KEY=<在 Dify 控制台创建应用后拿到的 API Key>
CS_DATASET_ID=<控制台创建 CS_KB 知识库的 ID>
SALES_DATASET_ID=<控制台创建 Sales_KB 知识库的 ID>
```

### 8.2 本地防火墙放行 8000(供服务器 Dify 工作流回调)

在本地开发机(管理员 PowerShell)执行:

```powershell
netsh advfirewall firewall add rule name="agent_cs backend 8000" dir=in action=allow protocol=TCP localport=8000
```

并确认 backend 启动时监听 0.0.0.0(uvicorn `--host 0.0.0.0`)。

### 8.3 Dify 控制台配置(浏览器操作)

1. 登录 `http://192.168.110.16:3080`
2. 创建知识库 `CS_KB` 与 `Sales_KB`,把两个 Dataset ID 填入本地 `.env`(见 8.1)
3. 导入工作流(仓库 `dify-workflows/` 下的 `cs_agent_chatbot.yml` / `sales_copilot_chatbot.yml` / `sales_case_extractor.yml`)
4. 在应用的"环境变量"里,把 `BACKEND_BASE_URL` 从 `http://host.docker.internal:8000`
   改为 `http://<本地开发机局域网IP>:8000`(Dify 在服务器上,`host.docker.internal` 已不再指向你的开发机)

### 8.4 端到端验证

```bash
# ① 本地 backend 能否调服务器 Dify API
curl http://192.168.110.16:5081/v1/datasets/{CS_DATASET_ID} \
     -H "Authorization: Bearer $DIFY_API_KEY"

# ② 本地跑一次知识审核同步(APPROVED → BackgroundTasks 同步 Dify),看 backend 日志 dify_sync_success
# ③ 服务器 Dify 手动运行一次工作流,确认 HTTP 回调打到本地 backend(本地日志出现 /api/v1/cs/unanswered/capture)
```

---

## 9. 常见问题

| 症状 | 处理 |
|---|---|
| 3080/5081 被占用 | 改 `.env.dify` 的 `WEB_PORT` / `API_PORT`,重启 |
| 控制台能开但 API 报 localhost | `.env.dify` 的 `CONSOLE_API_URL`/`APP_API_URL` 必须是 `http://192.168.110.16:5081`(不是 localhost),改后 `up -d` 重建 web |
| 数据没落在 /data | 检查 `DIFY_DATA_ROOT` 是否为绝对路径且目录已建 |
| 工作流回调失败(502/超时) | 本地开发机是否开机?防火墙是否放行 8000?`BACKEND_BASE_URL` 是否已改为开发机局域网 IP? |
| 容器权限报错(storage) | `init_permissions` 会 chown 1001:1001;若失败手动 `chown -R 1001:1001 $BASE/docker/volumes/app/storage` |
| 微信回调链路 | 微信 → 云 nginx(wx.fmtcloud.cn) → frps 6001 → frpc → 本地 backend:8000,与本次 Dify 部署无关,保持不变 |

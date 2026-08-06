# Dify 官方 Docker 部署手册（推荐）· 1.16.1

> **推荐路径**：不再使用仓库内的 `docker-compose.dify.yml`（自定义精简版，曾漏配
> `PLUGIN_DAEMON_URL`/`PLUGIN_DAEMON_KEY` 导致 `Failed to request plugin daemon` 400、
> 需手工维护 CORS 白名单）。改用官方 `docker/` 目录 + `.env` 单文件配置，
> nginx 统一入口（控制台与 API 同域，无跨域问题），所有服务配置由官方维护。
>
> 本手册以「**保留现有数据**」为前提（管理员账号/工作区直接复用旧 volumes）。

---

## 0. 官方方案与旧自定义方案的差异（为什么换）

| 项 | 旧自定义 compose | 官方 compose（本手册） |
|---|---|---|
| 入口 | web:3080 + api:5081 两个端口，浏览器直连 api 跨域，需手工配 `CONSOLE_CORS_ALLOW_ORIGINS` | nginx 统一入口，控制台/API/`/v1`/插件端点同域，`CONSOLE_CORS_ALLOW_ORIGINS=*`，**一个端口 3080** |
| api→plugin_daemon | 环境变量漏 `PLUGIN_DAEMON_URL`/`PLUGIN_DAEMON_KEY`，api 默认连 `localhost:5002` 失败 → 模型供应商页 400 | `.env` 内置 `PLUGIN_DAEMON_URL=http://plugin_daemon:5002` + `PLUGIN_DAEMON_KEY`，env 自动注入 |
| 配套服务 | 缺 nginx/ssrf_proxy/api_websocket/worker_beat | 官方全套（nginx、ssrf_proxy、agent_backend、local_sandbox、api_websocket、worker_beat） |
| 镜像差异 | redis:7-alpine、weaviate:1.19.0、postgres:15(Debian) | redis:6-alpine、weaviate:1.27.0、postgres:15-alpine（见 §4 数据兼容处理） |

## 1. 前置检查（服务器执行）

```bash
cat /etc/os-release          # 确认发行版/内核（CentOS7 3.10 老内核必须保留 §6 override）
docker compose version       # 官方 compose 用了 env_file 的 path 语法，必须 ≥ v2.24.0
df -h /data                  # 空间充足（数据 5-20GB）
ss -ltn | grep -E ':(3080|443)'   # 确认端口空闲
```

若 `docker compose version` 低于 v2.24：升级 docker-compose-plugin（或用 `docker-compose` 的较新独立二进制）。

## 2. 停旧部署（保留数据卷！）

```bash
BASE=/data/data2025/fmt_software/fmt-infra/dify
cd "$BASE"
docker compose -f docker-compose.dify.yml --env-file .env.dify down
# 千万不要 docker compose down -v，也不要删 docker/volumes —— 那是数据
```

（可选）备份 pg 数据：`tar -C "$BASE/docker/volumes/db" -czf /data/db_backup_$(date +%F).tgz data`

## 3. 获取官方 1.16.1 docker 目录

```bash
cd "$BASE"
git clone --depth 1 --branch 1.16.1 https://github.com/langgenius/dify.git dify-official
```

服务器无 git / 无法访问 GitHub 时：本地开发机下载
`https://github.com/langgenius/dify/archive/refs/tags/1.16.1.tar.gz`，
解压后仅 scp `docker/` 目录（含 `envs/`、`nginx/`、`ssrf_proxy/`）上去。

## 4. 数据卷软链（复用旧数据）

```bash
cd "$BASE/dify-official/docker"
ln -s "$BASE/docker/volumes" volumes
```

兼容性核对（官方卷结构 vs 旧数据）：

| 卷 | 官方挂载 | 旧数据 | 结论 |
|---|---|---|---|
| pg 元数据 | `./volumes/db/data`（PGDATA=.../pgdata） | 同结构 | ✅ 直接复用（账号/工作区/插件库 dify_plugin 都在里面） |
| app storage | `./volumes/app/storage` | 同结构 | ✅ 直接复用 |
| plugin_daemon | `./volumes/plugin_daemon` | 同结构（镜像同为 0.6.3-local） | ✅ 直接复用 |
| sandbox | `./volumes/sandbox/{dependencies,conf}` | 同结构（conf/config.yaml 已预置） | ✅ 直接复用 |
| weaviate | `./volumes/weaviate` | 1.19 数据 | ✅ 1.27 向后兼容读 1.19；若从未建知识库则为空，无风险 |
| redis | `./volumes/redis/**data**` | 旧数据在 `./volumes/redis/` 根（redis 7 格式） | ⚠️ **有意不迁移**：官方挂 `redis/data`（自动新建空目录）。redis 只是缓存/队列，全新初始化即可，同时规避 redis 6 读不了 redis 7 AOF 的问题 |

## 5. 准备 .env（关键：密钥与旧部署一致）

```bash
cd "$BASE/dify-official/docker"
cp .env.example .env
```

必改项（对照服务器上现用的旧 `$BASE/.env.dify` 抄值，**保留数据时密钥必须一致**）：

```ini
# ① 端口：统一入口 3080（nginx 容器内仍是 80）
EXPOSE_NGINX_PORT=3080

# ② 以下全部抄旧 .env.dify（若旧部署未改过，.env.example 默认值与旧值相同，可不动）：
SECRET_KEY=                    # 抄旧值（旧数据加密字段依赖它）
DB_PASSWORD=                   # 与旧 pg 一致，否则连不上旧数据
REDIS_PASSWORD=                # 与旧一致（虽然 redis 数据不迁移，但保持统一）
WEAVIATE_API_KEY=              # 与旧 weaviate 一致
SANDBOX_API_KEY=               # 与旧一致（须与 volumes/sandbox/conf/config.yaml 的 app.key 相同）
PLUGIN_DAEMON_KEY=             # api↔plugin_daemon 鉴权，两侧由同一变量注入
PLUGIN_DIFY_INNER_API_KEY=     # 同上
DIFY_AGENT_API_TOKEN=          # agent 内部 token
DIFY_AGENT_SERVER_SECRET_KEY=  # 必须 base64url 且解码后恰好 32 字节

# ③ 局域网访问 websocket（默认 ws://localhost 只适合本机访问）
NEXT_PUBLIC_SOCKET_URL=ws://192.168.110.16:3080

# ④ 容器命名：容器前缀 = compose 项目名（默认取目录名 docker，改掉避免与其他栈混淆）
COMPOSE_PROJECT_NAME=dify-official
```

> 其余全部保持 `.env.example` 默认：`COMPOSE_PROFILES` 会自动带上
> `weaviate,postgresql,collaboration`；`CONSOLE_API_URL` 留空（同域相对路径）。

### 改名注意（COMPOSE_PROJECT_NAME）

必须先停旧容器再改，否则会同时出现 `docker-*` 和 `dify-official-*` 两套容器抢端口：

```bash
docker compose down                       # ① 先停（当前前缀还是 docker）
echo 'COMPOSE_PROJECT_NAME=dify-official' >> .env   # ② 再改
docker compose config | head -3           # ③ 确认 name: dify-official
docker compose up -d                      # ④ 重新拉起，容器名变为 dify-official-*
```

影响：数据卷是 bind mount（`./volumes/...`），容器重建不影响数据；网络名变为
`dify-official_default`，容器间仍按服务名互访，无影响。

## 6. override 文件（兼容补丁 + 内存限制，必须放）

在 `docker/` 目录新建 `docker-compose.override.yaml`（与 `docker-compose.yaml` 同目录，
compose 自动合并），包含两部分：

### 6.1 db_postgres 兼容补丁

官方 `db_postgres` 是 `postgres:15-alpine`（容器 uid=70，旧数据属主是 Debian 版 uid=999，
且 CentOS7 老内核默认 seccomp 会拦 postgres 写 pg_wal）：

```yaml
services:
  db_postgres:
    image: postgres:15          # Debian 变体(glibc)：uid 999 与旧数据属主一致；兼容 CentOS7 老内核
    security_opt:
      - seccomp=unconfined      # 老内核(3.10)下 postgres 写 pg_wal 报 Operation not permitted 的修复；内网部署风险可控
```

> 仅覆盖 `db_postgres`，其余服务全部保持官方镜像。确认过不是老内核/非 CentOS7 时
> 此文件仍无害（Debian 版 postgres 只是更保守）。

### 6.2 全服务内存限制（防宿主 OOM，必配）

> 教训：曾有线上服务器未配容器内存限制，进程吃光宿主内存触发**宿主级 OOM**，
> 服务器直接崩掉而非单个容器被杀。容器限制在 cgroup 内生效：单容器超限只杀该容器
> 并由 `restart: always` 拉起，**不会拖垮宿主**。

在同一 override 文件里追加（限制值为当前实际占用的 3-8 倍，正常负载不会触发）：

```yaml
  db_postgres:
    deploy:
      resources:
        limits:
          memory: 2g

  api:
    deploy:
      resources:
        limits:
          memory: 2g

  api_websocket:
    deploy:
      resources:
        limits:
          memory: 1g

  worker:
    deploy:
      resources:
        limits:
          memory: 2g

  worker_beat:
    deploy:
      resources:
        limits:
          memory: 1g      # 官方默认 512m 偏紧（空闲已占 ~300MiB），留余量给定时任务高峰

  web:
    deploy:
      resources:
        limits:
          memory: 1g

  nginx:
    deploy:
      resources:
        limits:
          memory: 256m

  redis:
    deploy:
      resources:
        limits:
          memory: 512m

  weaviate:
    deploy:
      resources:
        limits:
          memory: 2g      # 大批量索引文档时内存会明显上涨

  sandbox:
    deploy:
      resources:
        limits:
          memory: 512m

  plugin_daemon:
    deploy:
      resources:
        limits:
          memory: 1g

  agent_backend:
    deploy:
      resources:
        limits:
          memory: 1g

  local_sandbox:
    deploy:
      resources:
        limits:
          memory: 512m

  ssrf_proxy:
    deploy:
      resources:
        limits:
          memory: 256m

  agent_ssrf_proxy:
    deploy:
      resources:
        limits:
          memory: 256m
```

限制生效：

```bash
docker compose config | grep -B 3 -A 5 'memory:'   # 确认 override 合并正确
docker compose up -d                               # 配置变化会自动重建容器（数据不受影响）
docker stats --no-stream                           # 验证 LIMIT 列
```

> 某容器频繁被 OOM 杀（`docker logs` 见 `Killed`）时，单独调大该服务限制即可。

## 7. 启动与验证

```bash
cd "$BASE/dify-official/docker"
docker compose up -d
docker compose ps                 # 全部 running/healthy（首次迁移 1-3 分钟）
docker compose logs -f --tail 50  # 观察 db/plugin_daemon 是否报错

curl -sI http://127.0.0.1:3080 | head -1        # 200（或 307 重定向到 /signin，正常）
docker compose exec api curl -s localhost:5001/health   # API 健康（nginx 不转发 /health，勿 curl 3080/health）
```

浏览器访问 `http://192.168.110.16:3080`，用原账号登录（数据保留，无需重新注册）。
进入「设置 → 模型供应商」应能正常加载（不再 400）。

## 8. 本地 backend 切换（开发机）

仓库根目录 `.env` 改一行（API 从 5081 改为 nginx 统一入口 3080）：

```ini
DIFY_BASE_URL=http://192.168.110.16:3080/v1
```

注意：`/console/api`、`/v1`、`/e/{hook_id}`（插件端点）现在都走 3080，5081 不再暴露。

## 9. 回滚

```bash
cd "$BASE/dify-official/docker" && docker compose down     # 停官方栈（不删卷）
cd "$BASE" && docker compose -f docker-compose.dify.yml --env-file .env.dify up -d
```

旧数据卷从未被覆盖（软链只读复用），可随时回滚。

## 10. 常见问题

| 症状 | 处理 |
|---|---|
| `docker compose` 报 `env_file` 语法不支持 | compose 版本 < v2.24，升级 docker-compose-plugin |
| db_postgres 起不来，日志 `could not write to file ... Operation not permitted` | 检查 override 是否生效（`docker compose config | grep seccomp`）；SELinux enforcing 时 `sudo chcon -Rt container_file_t $BASE/docker/volumes` |
| weaviate 报旧数据不兼容 | 知识库未建过则直接 `rm -rf volumes/weaviate/*` 全新初始化 |
| 登录报错/接口 500，api 日志 `relation ... does not exist` | `docker compose exec api flask db upgrade`（保留数据一般不会出现） |
| 浏览器打开跳 `/install`（首次安装页） | 数据卷软链失败：官方仓库 clone 自带空 `volumes` 目录，`ln -s` 报 `File exists`，db 在空目录初始化了新库。修复：`docker compose down` → `rm -rf volumes` → `ln -s $BASE/docker/volumes volumes` → `up -d`。旧数据始终在 `$BASE/docker/volumes`，未丢失 |
| `docker compose up` 报 `0.0.0.0:80: bind: address already in use` | `.env` 里 `EXPOSE_NGINX_PORT` 未改成 3080（默认 80 被占用）；改后 `up -d`。443 同理（`EXPOSE_NGINX_SSL_PORT`） |
| 容器名还是 `docker-*` | `.env` 加 `COMPOSE_PROJECT_NAME=dify-official`，先 `down` 再 `up`（见 §5 改名注意） |
| 某容器反复被杀重启（日志 `Killed`/OOM） | 该服务内存限制偏小，单独调大 override 里的对应值 |
| 模型供应商页仍 400 | `docker compose logs api --tail 100` 看是否还有 plugin_daemon 相关报错；确认 `.env` 里 `PLUGIN_DAEMON_URL`/`PLUGIN_DAEMON_KEY` 已设置 |

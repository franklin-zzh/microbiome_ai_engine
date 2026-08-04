# FRP 内网穿透说明

本目录（`frp-client`）用于把本地开发机/内网的后端服务（端口 `8000`）通过 **FRP（Fast Reverse Proxy）** 隧道穿透到云服务器，让公网域名 `wx.fmtcloud.cn` 能够访问到本地服务。主要用于微信客服/回调等需要公网入口的场景。

---

## 1. FRP 是什么？

FRP（Fast Reverse Proxy）是一个开源的内网穿透工具，官网：<https://github.com/fatedier/frp>。

它的核心作用是：**把没有公网 IP 的内网机器上的服务，通过一台有公网 IP 的服务器暴露到互联网上。**

### 1.1 核心角色

```text

  公网用户/微信服务器            云服务器（有公网IP）              本地电脑/内网服务器
        │                              │                              │
        │  HTTPS 访问 wx.fmtcloud.cn   │                              │
        │────────────────────────────>│                              │
        │                              │  Nginx 443 端口               │
        │                              │  反向代理到 127.0.0.1:6001    │
        │                              │                              │
        │                              │  FRP 服务端 (frps)           │
        │                              │  监听 7001 端口               │
        │                              │<─────────────────────────────│  FRP 客户端 (frpc)
        │                              │      建立加密控制隧道         │        监听本地 8000
        │                              │                              │        映射到远端 6001
        │                              │  数据流量通过 FRP 隧道转发    │
        │                              │────────────────────────────>│
        │<────────────────────────────│                              │
        │     最终到达本地 8000 端口   │                              │

```

- **frps（FRP Server）**：运行在有公网 IP 的云服务器上，监听客户端连接，并接收公网流量。
- **frpc（FRP Client）**：运行在内网机器（你的开发电脑或内网服务器）上，连接 frps，把本地端口映射到公网服务器端口。
- **Nginx**：在云服务器上，把 `wx.fmtcloud.cn` 的 HTTPS 443 端口反向代理到 FRP 远端端口 `6001`。

---

## 2. 目录结构

```text
frp-client/
├── docker-compose.yml          # 本地 frpc 客户端启动文件
├── frpc.toml                   # 本地 frpc 配置文件
└── frp/                        # 云服务器 frps 服务端配置（也放在本目录里做版本管理）
    ├── docker-compose.yml      # 服务端 frps 启动文件
    ├── frps.toml                # 服务端 frps 配置文件
    └── wx.fmtcloud.cn.conf     # 云服务器 Nginx 站点配置（反向代理到 6001）
```

---

## 3. 配置文件说明

### 3.1 服务端：frp/frps.toml

```toml
bindPort = 7000                 # frps 监听端口，frpc 通过该端口连接服务端
auth.token = "YOUR_FRP_AUTH_TOKEN"  # 认证令牌，客户端和服务端必须一致（替换为你的真实 token，勿提交真实值）
```

> 当前 `docker-compose.yml` 把宿主机的 `7001` 端口映射到容器内的 `7000` 端口，所以客户端 `serverPort` 写的是 `7001`。

### 3.2 客户端：frpc.toml

```toml
serverAddr = "39.97.252.180"    # 云服务器公网 IP
serverPort = 7001               # 云服务器暴露的 frps 端口（映射后的外部端口）
auth.token = "YOUR_FRP_AUTH_TOKEN"  # 必须与服务端一致（替换为你的真实 token，勿提交真实值）

[[proxies]]
name = "wechat-kf"              # 隧道名称
type = "tcp"                    # 协议类型
localIP = "host.docker.internal"  # 本地后端地址，在 Docker 里指宿主机的 localhost
localPort = 8000                # 本地后端服务端口（如 FastAPI/Flask/Django 等）
remotePort = 6001               # 云服务器上映射的端口，Nginx 会反向代理到它
```

### 3.3 服务端 Nginx：frp/wx.fmtcloud.cn.conf

```nginx
upstream wx_frp_backend {
    server 127.0.0.1:6001;      # 对应 frpc 里的 remotePort
}

server {
    listen 80;
    server_name wx.fmtcloud.cn;
    return 301 https://$host$request_uri;  # HTTP 重定向到 HTTPS
}

server {
    listen 443 ssl http2;
    server_name wx.fmtcloud.cn;

    # SSL 证书路径
    ssl_certificate     /data/fmtsw-certs/wildcard.fmtcloud.cn/fullchain.pem;
    ssl_certificate_key /data/fmtsw-certs/wildcard.fmtcloud.cn/privkey.pem;

    location / {
        proxy_pass http://wx_frp_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 30s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        client_max_body_size 50m;
    }

    access_log /var/log/nginx/wx.fmtcloud.cn.access.log;
    error_log  /var/log/nginx/wx.fmtcloud.cn.error.log;
}
```

---

## 4. 使用步骤

### 4.1 前提条件

- 一台有公网 IP 的云服务器（如阿里云 ECS，IP 为 `39.97.252.180`）。
- 服务器已安装 Docker、Docker Compose、Nginx。
- 服务器安全组已放行：
  - TCP `7001`（FRP 控制端口）。
  - TCP `80`、`443`（HTTP/HTTPS）。
- 本地机器已安装 Docker、Docker Compose。
- 本地后端服务已启动并监听 `8000` 端口。

### 4.2 启动 FRP 服务端（在云服务器上）

进入云服务器，把 `frp/` 目录下的文件放到服务器上（例如 `/opt/frp`）：

```bash
cd /opt/frp

# 注意：当前服务端配置文件名为 frp.toml，但 docker-compose.yml 里挂载的是 frps.toml
# 需要确保 /opt/frp/frps.toml 存在，或修改 compose 文件与现有文件名一致
docker compose up -d frps
```

检查服务端是否启动成功：

```bash
docker logs frps
```

### 4.3 启动 FRP 客户端（在本地开发机）

在本目录执行：

```bash
cd D:\projects\microbiome_ai_engine\frp-client
docker compose up -d frpc
```

检查客户端是否连接成功：

```bash
docker logs frpc
```

正常日志中应包含类似 “proxy started successfully” 或 “connect to server success” 的信息。

### 4.4 配置 Nginx（在云服务器上）

把 `frp/wx.fmtcloud.cn.conf` 放到 Nginx 配置目录（如 `/etc/nginx/conf.d/`），然后重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 4.5 验证链路

1. 本地确保后端已启动，访问 `http://127.0.0.1:8000` 能正常响应。
2. 在云服务器上验证本地端口是否通：

   ```bash
   curl http://127.0.0.1:6001
   ```

   如果返回本地后端的内容，说明 FRP 隧道已建立。
3. 在外网访问：

   ```bash
   curl https://wx.fmtcloud.cn
   ```

   如果返回本地后端的内容，说明整条链路已打通。

---

## 5. 常见问题与排查

### 5.1 客户端连不上服务端

- 检查云服务器安全组是否放行 `7001`。
- 检查服务端是否启动：`docker logs frps`。
- 检查 `frpc.toml` 中的 `serverAddr` 和 `serverPort` 是否正确。
- 检查客户端与服务端的 `auth.token` 是否完全一致。

### 5.2 本地端口访问不通

- 确认本地后端确实监听在 `8000` 端口。
- 确认 `frpc` 容器能通过 `host.docker.internal` 访问到宿主机（Windows 下 Docker Desktop 默认支持）。
- 在 Linux 上若 `host.docker.internal` 不生效，可将 `localIP` 改为宿主机局域网 IP，或改用 `--network host` 模式。

### 5.3 Nginx 返回 502

- 检查 `frpc` 是否已启动并建立隧道。
- 检查 Nginx 配置的 `upstream` 端口是否为 `6001`。
- 查看 Nginx 错误日志：`tail -f /var/log/nginx/wx.fmtcloud.cn.error.log`。

### 5.4 证书路径问题

Nginx 配置中使用了通配符证书 `/data/fmtsw-certs/wildcard.fmtcloud.cn/`，请确保证书文件实际存在，否则 Nginx 会启动失败。

---

## 6. 注意事项

- `auth.token` 是 FRP 隧道的认证密钥，**务必妥善保管**，不要泄露到公开仓库。
- 如果服务端和客户端在不同的网络环境，建议启用 TLS 加密（新版 frp 默认已开启 TLS）。
- 本地调试时若设置了断点，Nginx 的 `proxy_read_timeout` 已设置为 300s，防止长时间调试时报 504。
- 当前服务端 `docker-compose.yml` 里挂载的是 `frps.toml`，但实际文件名为 `frp.toml`，部署前请确认两者名称一致，否则会导致服务端启动失败。

---

## 7. 相关链接

- FRP 官方仓库：<https://github.com/fatedier/frp>
- FRP 官方文档：<https://gofrp.org/zh-cn/docs/>
- Docker 镜像：`snowdreamtech/frps` / `snowdreamtech/frpc`

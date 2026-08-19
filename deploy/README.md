# agent_cs 生产环境部署包说明文档

本目录（`deploy/`）包含了将 `agent_cs` 后端部署至阿里云服务器的**全套轻量化交付文件**。

---

## 📁 目录清单

```text
deploy/
├── .dockerignore              # Docker 打包忽略规则（排除 .venv、缓存、本地测试文件与密钥）
├── .env.prod.example          # 生产环境变量配置模板
├── docker-compose.prod.yml    # 生产 Docker 编排（带 512MB 内存硬限制、60MB 日志上限、13009 端口）
├── package_backend.bat        # Windows 本地一键打包脚本（导出为类似 JAR 的单个 tar 镜像包）
├── package_backend.ps1        # PowerShell 本地打包脚本
├── deploy_on_server.sh        # 阿里云服务器一键导入与启动脚本
├── nginx/
│   └── wx.fmtcloud.cn.conf    # 阿里云大 Nginx 转发配置（反代 13009 端口 + 微信回调 + SSL）
└── README.md                  # 本操作说明
```

---

## 🚀 部署全流程（极简 3 步）

### 第一步：在本地电脑打包（像打包 Jar 一样）

在本地电脑上，双击运行：
👉 **`deploy/package_backend.bat`**  
（或者在 PowerShell 中运行 `.\deploy\package_backend.ps1`）

脚本将自动：
1. 依据 `.dockerignore` 过滤所有不需要的本地临时文件与虚拟环境；
2. 构建精简版 Docker 生产镜像；
3. 导出为单文件交付包：**`deploy/agent_cs_backend.tar`**。

---

### 第二步：将部署文件上传至阿里云服务器

通过 **WinSCP / FinalShell / MobaXterm / SCP**，在服务器上创建目录 `/opt/microbiome_ai_engine/`，并将本地 `deploy/` 下的以下文件上传上去：

- `agent_cs_backend.tar` （核心镜像交付包）
- `docker-compose.prod.yml`
- `.env.prod.example`
- `deploy_on_server.sh`

---

### 第三步：在阿里云服务器上配置并启动

在服务器终端执行：

```bash
# 1. 进入部署目录
cd /opt/microbiome_ai_engine

# 2. 创建真实生产环境变量文件
cp .env.prod.example .env.prod
nano .env.prod  # 填入真实数据库密码、Redis 密码及微信客服密钥

# 3. 运行一键部署脚本
bash deploy_on_server.sh
```

**该脚本将自动完成：**
1. 导入 `agent_cs_backend.tar` 镜像；
2. 执行 Alembic 数据库 Schema 迁移（自动在 MySQL 中建齐所有表和索引）；
3. 启动 2 个 Worker 并监听 `127.0.0.1:13009`；
4. 施加严格的 **512MB 内存配额限制** 与 **60MB 日志轮转上限**；
5. 执行 `/health` 健康检查与资源占用验证。

---

### 第四步：配置阿里云大 Nginx 转发

将 `deploy/nginx/wx.fmtcloud.cn.conf` 的内容加入到您服务器现有的大 Nginx 配置中（或放入 `/etc/nginx/conf.d/`），然后执行：

```bash
nginx -t && nginx -s reload
```

---

## 🔍 运维与检查命令

- **查看容器资源占用（内存/CPU）**：
  ```bash
  docker stats agent_cs_backend --no-stream
  ```
- **查看后端实时日志**：
  ```bash
  docker logs -f --tail 100 agent_cs_backend
  ```
- **服务健康探活**：
  ```bash
  curl -i http://127.0.0.1:13009/health
  # 或公网域名
  curl -i https://wx.fmtcloud.cn/health
  ```
- **停止/重启服务**：
  ```bash
  docker compose -f docker-compose.prod.yml restart
  ```

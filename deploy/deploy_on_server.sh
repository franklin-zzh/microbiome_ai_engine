#!/usr/bin/env bash
# ==============================================================================
# 阿里云服务器一键部署 / 更新脚本 (agent_cs)
# ==============================================================================

set -e

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DEPLOY_DIR"

echo "=============================================================================="
echo "[agent_cs] 正在执行云端部署..."
echo "=============================================================================="

# 1. 检查环境配置文件
if [ ! -f ".env.prod" ]; then
    if [ -f ".env.prod.example" ]; then
        echo "[提示] 未检测到 .env.prod，正在基于 .env.prod.example 创建..."
        cp .env.prod.example .env.prod
        echo "[警告] 请先编辑 .env.prod 填入真实数据库密码及密钥后再重新运行本脚本！"
        exit 1
    else
        echo "[错误] 缺少 .env.prod 配置文件。"
        exit 1
    fi
fi

# 2. 创建运行所需数据与日志目录
mkdir -p data logs

# 3. 加载 tar 镜像（如果存在）
if [ -f "agent_cs_backend.tar" ]; then
    echo "[1/3] 发现镜像包 agent_cs_backend.tar，正在导入 Docker 镜像..."
    docker load -i agent_cs_backend.tar
    echo "[1/3] 镜像导入完成。"
else
    echo "[1/3] 未找到 tar 包，尝试直接基于本地 Dockerfile 构建..."
    docker compose -f docker-compose.prod.yml build
fi

# 4. 确保内部网络存在并启动服务
if ! docker network ls | grep -q "fmtsw-infra_fmtsw-net"; then
    echo "[提示] 检测到 fmtsw-infra_fmtsw-net 尚未创建，正在创建内部网络..."
    docker network create fmtsw-infra_fmtsw-net || true
fi

echo "[2/3] 正在启动 agent_cs 后端服务..."
docker compose -f docker-compose.prod.yml down --remove-orphans || true
docker compose -f docker-compose.prod.yml up -d

# 5. 探活验证
echo "[3/3] 正在验证服务健康状态 (等待 5 秒)..."
sleep 5

HEALTH_STATUS=$(curl -s http://127.0.0.1:13006/health || echo "failed")
echo "健康检查响应: $HEALTH_STATUS"

if [[ "$HEALTH_STATUS" =~ "ok" ]]; then
    echo "=============================================================================="
    echo "✅ 部署成功！后端服务正在运行于 127.0.0.1:13006"
    echo "当前容器资源状态："
    docker stats agent_cs_backend --no-stream
    echo "=============================================================================="
else
    echo "⚠️ 探活未返回 ok，请检查日志: docker logs --tail 50 agent_cs_backend"
fi

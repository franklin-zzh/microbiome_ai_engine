from functools import lru_cache
from typing import Optional

import redis
from redis import Redis

from app.core.config import get_settings

settings = get_settings()

# 全局 Redis 客户端（连接池由 redis-py 内部管理）
_redis_client: Optional[Redis] = None


def get_redis() -> Redis:
    """获取 Redis 客户端单例（懒加载）"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=3,
            retry_on_timeout=True,
        )
    return _redis_client


def redis_health() -> dict:
    """Redis 健康检查（供 /health 与探活使用）"""
    try:
        client = get_redis()
        client.ping()
        return {"redis": "ok"}
    except Exception as exc:
        return {"redis": f"error: {exc}"}

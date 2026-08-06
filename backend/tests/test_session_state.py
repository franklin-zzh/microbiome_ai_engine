"""cs_session_state 生命周期测试：滚动过期、DB 兜底恢复、惰性清理

- 表结构由 conftest.migrated_test_db 通过 alembic upgrade head 建好（mb_ai_engine_test）
- Redis 使用本地实例（db3），测试用唯一 session_id，teardown 清理测试 key，不污染生产数据
"""
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.agent_cs import services as sm
from app.agent_cs.models import SessionState
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_access_token
from main import app

settings = get_settings()
client = TestClient(app)
redis = Redis.from_url(settings.redis_url)

# P0 鉴权：内部链路接口带 X-API-Key，管理读接口带 admin token
INTERNAL_HEADERS = {"X-API-Key": settings.internal_api_key}
ADMIN_HEADERS = {"Authorization": f"Bearer {create_access_token('pytest-admin')}"}

# 与 test_knowledge_api 一致：请求级 db 依赖指向测试库（conftest 已建表）
engine = create_engine(settings.test_database_url)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
TTL = settings.session_ttl_seconds  # 1800


def _route(session_id: str, message: str = "普通问题", channel: str = "WXKF", open_id: str = "test-open") -> dict:
    resp = client.post(f"/api/v1/chat/session/{session_id}/route", json={
        "user_message": message,
        "channel": channel,
        "open_id": open_id,
    }, headers=INTERNAL_HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _get_row(session_id: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT state, expires_at, updated_at FROM cs_session_state WHERE session_id=:s"),
            {"s": session_id},
        ).mappings().first()
    return row


def _redis_state(session_id: str) -> str:
    key = sm.SESSION_STATE_KEY.format(session_id=session_id)
    raw = redis.get(key)
    return raw.decode() if raw else None


def _cleanup_redis(session_id: str) -> None:
    redis.delete(
        sm.SESSION_STATE_KEY.format(session_id=session_id),
        sm.SESSION_NEG_KEY.format(session_id=session_id),
        sm.SESSION_CTX_KEY.format(session_id=session_id),
    )


def test_route_creates_state_with_expires_at():
    sid = f"t-{uuid.uuid4().hex[:12]}"
    try:
        result = _route(sid)
        assert result["action"] == "queue_for_ai"

        row = _get_row(sid)
        assert row is not None
        assert row["state"] == "NORMAL"
        # expires_at ≈ now + TTL（滚动语义），容差 120s
        expect = datetime.now() + timedelta(seconds=TTL)
        assert abs((row["expires_at"] - expect).total_seconds()) < 120
    finally:
        _cleanup_redis(sid)


def test_route_refreshes_expires_at():
    sid = f"t-{uuid.uuid4().hex[:12]}"
    try:
        _route(sid)

        # 把 expires_at 拨到 1 小时前（明显旧值），再路由应滚动刷新到 now+TTL。
        # 两次路由落在同一秒时 MySQL DATETIME 秒级截断会让 now+TTL 相等，
        # 故用"拨旧后的值"作基线：now-1h 与 now+30min 差 54 分钟，截断下也必然可区分
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE cs_session_state SET expires_at=:e WHERE session_id=:s"),
                {"e": datetime.now() - timedelta(hours=1), "s": sid},
            )
        stale = _get_row(sid)["expires_at"]

        _route(sid)
        refreshed = _get_row(sid)["expires_at"]
        assert refreshed > stale, "expires_at 应滚动刷新（路由后比旧值更晚）"
    finally:
        _cleanup_redis(sid)


def test_restore_state_from_db_after_redis_expire():
    """Redis 状态丢失（模拟过期/重启）后，HUMAN_MODE 应从 DB 恢复，AI 不重新抢答"""
    sid = f"t-{uuid.uuid4().hex[:12]}"
    try:
        result = _route(sid, message="我要转人工")
        assert result["route"]["state"] == "HUMAN_MODE"
        assert result["action"] == "pass_to_human"
        assert _redis_state(sid) == "HUMAN_MODE"

        # 模拟 Redis 过期：删掉 state key（DB 行保留）
        redis.delete(sm.SESSION_STATE_KEY.format(session_id=sid))
        assert _redis_state(sid) is None

        # 再来一条普通消息：应从 DB 恢复 HUMAN_MODE，仍不抢答
        result = _route(sid, message="在吗")
        assert result["route"]["state"] == "HUMAN_MODE"
        assert result["action"] == "pass_to_human"
        assert _redis_state(sid) == "HUMAN_MODE", "恢复后 Redis 应重新持有 HUMAN_MODE"
    finally:
        _cleanup_redis(sid)


def test_lazy_cleanup_removes_expired_rows():
    """不活跃超过保留期（30 天）的行在任意 route 时被惰性清理"""
    old_sid = f"t-old-{uuid.uuid4().hex[:8]}"
    new_sid = f"t-new-{uuid.uuid4().hex[:8]}"
    try:
        _route(old_sid)
        assert _get_row(old_sid) is not None

        # 把 old 行改为 31 天前过期（模拟不活跃超保留期）
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE cs_session_state SET expires_at=:e WHERE session_id=:s"),
                {"e": datetime.now() - timedelta(days=31), "s": old_sid},
            )

        # 任意一次 route 触发惰性清理
        _route(new_sid)
        assert _get_row(old_sid) is None, "过期超保留期的行应被惰性清理删除"
        assert _get_row(new_sid) is not None, "新会话行不应被误删"
    finally:
        _cleanup_redis(old_sid)
        _cleanup_redis(new_sid)


def test_get_session_state_falls_back_to_db():
    """Redis 缺失时 GET /session 展示 DB 中最后持久化的状态"""
    sid = f"t-{uuid.uuid4().hex[:12]}"
    try:
        _route(sid, message="我要转人工")
        redis.delete(sm.SESSION_STATE_KEY.format(session_id=sid))

        resp = client.get(f"/api/v1/chat/session/{sid}", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["state"] == "HUMAN_MODE"
    finally:
        _cleanup_redis(sid)


def test_risk_keyword_overrides_human_request():
    """同一条消息同时含风险词+转人工:医疗风控优先,必须 BLOCKED(不得落入 HUMAN_MODE)"""
    sid = f"t-{uuid.uuid4().hex[:12]}"
    try:
        result = _route(sid, message="我拉鲜血便,快给我转人工!")
        assert result["route"]["state"] == "BLOCKED"
        assert result["route"]["hit_keyword"] == "鲜血便"
        assert result["action"] == "pass_to_human"
        assert _redis_state(sid) == "BLOCKED"
    finally:
        _cleanup_redis(sid)


def test_risk_keyword_upgrades_human_mode_to_blocked():
    """HUMAN_MODE 状态下再发风险词:升级为 BLOCKED(医疗风控覆盖已转人工)"""
    sid = f"t-{uuid.uuid4().hex[:12]}"
    try:
        result = _route(sid, message="我要转人工")
        assert result["route"]["state"] == "HUMAN_MODE"

        result = _route(sid, message="我现在剧烈腹痛")
        assert result["route"]["state"] == "BLOCKED"
        assert result["route"]["hit_keyword"] == "剧烈腹痛"
        assert result["action"] == "pass_to_human"
        assert _redis_state(sid) == "BLOCKED"
    finally:
        _cleanup_redis(sid)

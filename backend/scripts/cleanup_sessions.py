"""会话状态清理脚本：删除不活跃超过保留期的 cs_session_state 行

与 router._lazy_cleanup_expired 同款清理逻辑，供手动/定时（W3 Celery 落地后）调用：
    python scripts/cleanup_sessions.py            # 实际删除
    python scripts/cleanup_sessions.py --dry-run  # 只统计将删行数，不删除

保留语义：expires_at（滚动刷新）= 最后活跃 + session_ttl_seconds；
行在 expires_at 早于 now - session_retention_days（默认 30 天）时删除。
历史 NULL 行用 updated_at 兜底。
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def cleanup(dry_run: bool = False) -> int:
    settings = get_settings()
    cutoff = datetime.now() - timedelta(days=settings.session_retention_days)

    from sqlalchemy import create_engine

    engine = create_engine(settings.database_url)
    count_sql = text(
        "SELECT COUNT(*) FROM cs_session_state "
        "WHERE COALESCE(expires_at, updated_at) < :cutoff"
    )
    delete_sql = text(
        "DELETE FROM cs_session_state "
        "WHERE COALESCE(expires_at, updated_at) < :cutoff"
    )

    with engine.begin() as conn:
        expired = conn.execute(count_sql, {"cutoff": cutoff}).scalar()
        print(f"Retention cutoff: {cutoff}  (retention_days={settings.session_retention_days})")
        print(f"Expired rows: {expired}")
        if not dry_run and expired:
            conn.execute(delete_sql, {"cutoff": cutoff})
            print(f"Deleted {expired} rows.")
        elif dry_run:
            print("Dry run: nothing deleted.")
    return expired


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean expired cs_session_state rows")
    parser.add_argument("--dry-run", action="store_true", help="只统计不删除")
    args = parser.parse_args()
    cleanup(dry_run=args.dry_run)

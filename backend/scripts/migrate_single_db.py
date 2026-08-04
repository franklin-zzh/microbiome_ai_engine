"""一次性迁移脚本：双库（mb_ai_core / mb_ai_cs）→ 单库（mb_ai_engine）

把旧库的表 RENAME 到 mb_ai_engine，并统一为 core_* / cs_* 前缀：
    mb_ai_core.knowledge_items          -> mb_ai_engine.core_knowledge_items
    mb_ai_core.unanswered_questions     -> mb_ai_engine.core_unanswered_questions
    mb_ai_core.sales_cases              -> mb_ai_engine.core_sales_cases
    mb_ai_cs.cs_chat_logs               -> mb_ai_engine.cs_chat_logs
    mb_ai_cs.session_state              -> mb_ai_engine.cs_session_state
    mb_ai_cs.leads_preview              -> mb_ai_engine.cs_leads_preview

MySQL RENAME TABLE 支持同实例跨库、原子执行（单条语句内），索引/外键随表迁移；
但索引名不会随表名变化，需额外 RENAME INDEX 统一为模型命名（ix_<表名>_<列>）。
迁移完成后：新库表结构与 Base.metadata 一致，执行 `alembic stamp head` 标记基线；
空库（测试/新部署）直接 `alembic upgrade head` 建全量表。
旧库 mb_ai_core / mb_ai_cs 迁移后保留为空库（可手工 DROP）。
"""
import os

import pymysql

# (旧库, 旧表, 新库, 新表)
TABLE_MOVES = [
    ("mb_ai_core", "knowledge_items", "mb_ai_engine", "core_knowledge_items"),
    ("mb_ai_core", "unanswered_questions", "mb_ai_engine", "core_unanswered_questions"),
    ("mb_ai_core", "sales_cases", "mb_ai_engine", "core_sales_cases"),
    ("mb_ai_cs", "cs_chat_logs", "mb_ai_engine", "cs_chat_logs"),
    ("mb_ai_cs", "session_state", "mb_ai_engine", "cs_session_state"),
    ("mb_ai_cs", "leads_preview", "mb_ai_engine", "cs_leads_preview"),
]

# RENAME TABLE 不会自动重命名索引，需显式 RENAME INDEX（元数据级，无锁），
# 使索引名与模型自动生成的 ix_<表名>_<列> 命名一致（alembic check 零差异）。
# cs_chat_logs 的索引名本就带 cs_ 前缀，无需处理。
INDEX_RENAMES = {
    "core_knowledge_items": [("ix_knowledge_items_id", "ix_core_knowledge_items_id")],
    "core_sales_cases": [("ix_sales_cases_id", "ix_core_sales_cases_id")],
    "core_unanswered_questions": [("ix_unanswered_questions_id", "ix_core_unanswered_questions_id")],
    "cs_leads_preview": [
        ("ix_leads_preview_created_at", "ix_cs_leads_preview_created_at"),
        ("ix_leads_preview_id", "ix_cs_leads_preview_id"),
        ("ix_leads_preview_open_id", "ix_cs_leads_preview_open_id"),
    ],
    "cs_session_state": [
        ("ix_session_state_id", "ix_cs_session_state_id"),
        ("ix_session_state_open_id", "ix_cs_session_state_open_id"),
        ("ix_session_state_session_id", "ix_cs_session_state_session_id"),
    ],
}


def rename_indexes(cur, db: str) -> None:
    """把 db 库内表的旧索引名统一为模型命名（幂等：旧索引不存在则跳过）。"""
    for table, renames in INDEX_RENAMES.items():
        cur.execute(
            "SELECT index_name FROM information_schema.statistics "
            "WHERE table_schema=%s AND table_name=%s GROUP BY index_name",
            (db, table),
        )
        existing = {r[0] for r in cur.fetchall()}
        for old, new in renames:
            if old in existing:
                cur.execute(
                    f"ALTER TABLE `{db}`.`{table}` RENAME INDEX `{old}` TO `{new}`"
                )
                print(f"RENAME INDEX {db}.{table}.{old} -> {new}")
            else:
                print(f"SKIP  INDEX {db}.{table}.{old}: 不存在")


def migrate():
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "fumate")

    conn = pymysql.connect(host=host, port=port, user=user, password=password, charset="utf8mb4")
    conn.autocommit(True)
    cur = conn.cursor()

    pending = []
    for src_db, src_table, dst_db, dst_table in TABLE_MOVES:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s",
            (src_db, src_table),
        )
        src_exists = cur.fetchone()[0] == 1
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s",
            (dst_db, dst_table),
        )
        dst_exists = cur.fetchone()[0] == 1
        if src_exists and not dst_exists:
            pending.append((src_db, src_table, dst_db, dst_table))
        elif src_exists and dst_exists:
            print(f"SKIP  {src_db}.{src_table}: 目标 {dst_db}.{dst_table} 已存在")
        else:
            print(f"SKIP  {src_db}.{src_table}: 源表不存在")

    if pending:
        sql = ", ".join(
            f"`{s}`.{t} TO `{d}`.{dt}" for s, t, d, dt in pending
        )
        print("RENAME TABLE " + sql)
        cur.execute("RENAME TABLE " + sql)
        print(f"Moved {len(pending)} tables -> mb_ai_engine")
    else:
        print("Nothing to move.")

    rename_indexes(cur, "mb_ai_engine")

    cur.close()
    conn.close()


if __name__ == "__main__":
    migrate()

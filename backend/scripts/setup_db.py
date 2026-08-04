"""MySQL 单库初始化脚本（agent_cs）

在本地 Docker 部署的 global-mysql8 (3306, root/fumate) 上创建：
    mb_ai_engine       统一 schema（core_* 知识库体系 + cs_* 客服会话体系 + 销售线索）
    mb_ai_engine_test  pytest 测试库

历史双库（mb_ai_core / mb_ai_cs / *_test）已合并，见 scripts/migrate_single_db.py。
表结构由 Alembic 迁移管理：alembic upgrade head（不再使用 create_all）。
"""
import os

import pymysql


def setup():
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "fumate")

    conn = pymysql.connect(host=host, port=port, user=user, password=password, charset="utf8mb4")
    conn.autocommit(True)
    cur = conn.cursor()

    databases = [
        ("mb_ai_engine", "统一 schema：core_* 知识库体系 + cs_* 客服会话体系"),
        ("mb_ai_engine_test", "pytest 测试库"),
    ]

    for db, desc in databases:
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{db}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        print(f"Database {db} ({desc}) ensured.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    setup()

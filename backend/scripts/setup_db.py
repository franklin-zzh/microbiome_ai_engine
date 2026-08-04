"""MySQL 双库初始化脚本（agent_cs）

在本地 Docker 部署的 global-mysql8 (3306, root/fumate) 上创建：
    mb_ai_core      公共用户 / 知识库索引（knowledge_items / unanswered_questions / sales_cases）
    mb_ai_cs        客服 Agent 专有库（cs_chat_logs / session_state / leads_preview）
    mb_ai_core_test 知识库体系测试库（pytest 使用）
    mb_ai_cs_test   客服会话体系测试库（pytest 使用）
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
        ("mb_ai_core", "公共用户 / 知识库索引"),
        ("mb_ai_cs", "客服 Agent 专有库"),
        ("mb_ai_core_test", "知识库体系测试库"),
        ("mb_ai_cs_test", "客服会话体系测试库"),
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

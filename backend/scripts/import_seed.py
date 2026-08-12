import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal
from app.knowledge.models import SalesCase, SalesCaseStatus


def import_sales_cases(path: str) -> int:
    db = SessionLocal()
    count = 0
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
        for item in items:
            db.add(
                SalesCase(
                    submitted_by="seed",
                    raw_chat_log="（种子案例，无原始聊天记录）",
                    customer_type=item["customer_type"],
                    core_objection=item["core_objection"],
                    breakthrough_logic=item["breakthrough_logic"],
                    follow_up_script=item["follow_up_script"],
                    extracted_summary=item,
                    status=SalesCaseStatus.PENDING,
                )
            )
            count += 1
        db.commit()
    finally:
        db.close()
    return count


if __name__ == "__main__":
    # 表结构统一由 Alembic 管理：先 upgrade head（幂等），不再使用 create_all
    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parent.parent
    command.upgrade(Config(str(backend_root / "alembic.ini")), "head")

    # 旧知识项链路已下线（2026-08-12）：CS FAQ 知识项不再支持 seed 导入
    # （一律走「原文件 → 逻辑文档/版本 → Dify」链路），此处仅导入销售案例原始记录。
    sales_count = import_sales_cases(str(backend_root / "seed" / "sales_script_seed.json"))
    print(f"Imported {sales_count} sales cases (PENDING status).")

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import CoreSessionLocal, core_engine
from app.knowledge.models import Base, KnowledgeItem, KnowledgeStatus, SalesCase, SalesCaseStatus


def import_cs_faq(path: str) -> int:
    db = CoreSessionLocal()
    count = 0
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
        for item in items:
            db.add(
                KnowledgeItem(
                    domain=item["domain"],
                    source_type="MANUAL",
                    status=KnowledgeStatus.PENDING,
                    title=item["title"],
                    question=item["question"],
                    answer=item["answer"],
                    tags=item.get("tags", []),
                    created_by="seed",
                )
            )
            count += 1
        db.commit()
    finally:
        db.close()
    return count


def import_sales_cases(path: str) -> int:
    db = CoreSessionLocal()
    count = 0
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
        for item in items:
            case = SalesCase(
                submitted_by="seed",
                raw_chat_log="（种子案例，无原始聊天记录）",
                customer_type=item["customer_type"],
                core_objection=item["core_objection"],
                breakthrough_logic=item["breakthrough_logic"],
                follow_up_script=item["follow_up_script"],
                extracted_summary=item,
                status=SalesCaseStatus.PENDING,
            )
            db.add(case)
            db.commit()
            db.refresh(case)

            answer = (
                f"【客户类型】{item['customer_type']}\n"
                f"【核心抗拒点】{item['core_objection']}\n"
                f"【成交破阻逻辑】{item['breakthrough_logic']}\n"
                f"【推荐跟进话术】{item['follow_up_script']}"
            )
            db.add(
                KnowledgeItem(
                    domain="SALES",
                    source_type="SALES_CASE",
                    status=KnowledgeStatus.PENDING,
                    title=f"销售案例：{item['customer_type']}",
                    question=None,
                    answer=answer,
                    tags=["销售案例", item["customer_type"]],
                    sales_case_id=case.id,
                    created_by="seed",
                )
            )
            count += 1
        db.commit()
    finally:
        db.close()
    return count


if __name__ == "__main__":
    Base.metadata.create_all(bind=core_engine)
    base_dir = Path(__file__).resolve().parent.parent
    cs_count = import_cs_faq(str(base_dir / "seed" / "cs_faq_seed.json"))
    sales_count = import_sales_cases(str(base_dir / "seed" / "sales_script_seed.json"))
    print(f"Imported {cs_count} CS FAQs and {sales_count} sales cases (PENDING status).")

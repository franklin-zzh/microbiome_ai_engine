"""知识库域（横切共享）：审核后同步 Dify 的业务编排"""
from typing import Optional

from sqlalchemy.orm import Session

from app.clients.dify_client import DifySyncError, sync_knowledge_to_dify
from app.core.database import CoreSessionLocal
from app.knowledge.models import KnowledgeItem, KnowledgeStatus


def sync_approved_knowledge(item_id: int, db: Optional[Session] = None) -> None:
    """审核通过后异步同步到 Dify（后台任务执行，失败仅记日志不阻塞）"""
    close_db = False
    if db is None:
        db = CoreSessionLocal()
        close_db = True
    try:
        item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
        if not item or item.status != KnowledgeStatus.APPROVED:
            return
        doc_id = sync_knowledge_to_dify(
            item_id=item.id,
            domain=item.domain.value,
            title=item.title,
            question=item.question,
            answer=item.answer,
            tags=item.tags,
            vector_doc_id=item.vector_doc_id,
        )
        if doc_id and doc_id != item.vector_doc_id:
            item.vector_doc_id = doc_id
            db.commit()
    except DifySyncError:
        # 失败已记录结构化日志，状态保持 APPROVED，便于后续重试
        pass
    finally:
        if close_db:
            db.close()

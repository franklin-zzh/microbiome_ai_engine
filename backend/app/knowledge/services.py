"""知识库域：审核/驳回后 Dify 同步的任务化编排（core_sync_tasks outbox）

设计（替代纯内存 BackgroundTasks）：
- 审核/驳回/下线接口只负责「注册对账任务」（QUEUED），不直接调 Dify；
- sync_approved_knowledge 注册后立即内联消费（后台线程内完成创建/更新+轮询），
  同时任务持久化在 core_sync_tasks —— 进程重启后由 process_sync_tasks 重放，
  解决「服务器重启导致同步任务静默丢失」问题；
- 失败自动退避重试（attempts < max_attempts 时 QUEUED + next_retry_at），
  超过上限置 FAILED（item.sync_status=FAILED + last_sync_error 可查）。
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.clients.dify_client import (
    DifyDocumentNotFound,
    DifySyncError,
    create_document,
    dataset_id_for_domain,
    delete_document,
    update_document,
    wait_document_indexed,
)
from app.core.database import SessionLocal
from app.core.logging import structured_log
from app.knowledge.models import (
    KnowledgeItem,
    KnowledgeStatus,
    SyncStatus,
    SyncTask,
    SyncTaskAction,
    SyncTaskStatus,
)

RETRY_BASE_SECONDS = 60


def enqueue_sync_task(db: Session, item: KnowledgeItem, action: SyncTaskAction) -> SyncTask:
    """注册对账任务（幂等）：同 item 已有进行中任务则不重复注册；
    已 COMPLETED 的 CREATE 再次注册时升级为 UPDATE。"""
    existing = (
        db.query(SyncTask)
        .filter(
            SyncTask.item_id == item.id,
            SyncTask.status.in_([SyncTaskStatus.QUEUED, SyncTaskStatus.RUNNING]),
        )
        .first()
    )
    if existing:
        return existing

    if action == SyncTaskAction.CREATE:
        done = (
            db.query(SyncTask)
            .filter(
                SyncTask.item_id == item.id,
                SyncTask.action == SyncTaskAction.CREATE,
                SyncTask.status == SyncTaskStatus.COMPLETED,
            )
            .first()
        )
        if done and item.vector_doc_id:
            action = SyncTaskAction.UPDATE

    task = SyncTask(item_id=item.id, action=action, status=SyncTaskStatus.QUEUED)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _mark_item_error(db: Session, item: KnowledgeItem, error: str, task: SyncTask) -> None:
    task.attempts = (task.attempts or 0) + 1
    task.last_error = error
    if task.attempts >= task.max_attempts:
        task.status = SyncTaskStatus.FAILED
        item.sync_status = SyncStatus.FAILED
        item.last_sync_error = error
    else:
        task.status = SyncTaskStatus.QUEUED
        task.next_retry_at = datetime.now() + timedelta(seconds=RETRY_BASE_SECONDS * (2 ** (task.attempts - 1)))
    db.commit()


def _execute_task(db: Session, task: SyncTask) -> bool:
    """执行单个对账任务。True=任务完结；False=失败待重试。"""
    item = db.query(KnowledgeItem).filter(KnowledgeItem.id == task.item_id).first()
    if not item:
        task.status = SyncTaskStatus.FAILED
        task.last_error = "knowledge item not found"
        db.commit()
        return True

    task.status = SyncTaskStatus.RUNNING
    db.commit()

    # 幂等保护：已有 doc id 的 CREATE 转 UPDATE（重试时避免重复建文档）
    if task.action == SyncTaskAction.CREATE and item.vector_doc_id:
        task.action = SyncTaskAction.UPDATE
        db.commit()

    try:
        if task.action == SyncTaskAction.CREATE:
            doc_id = create_document(item.id, item.domain.value, item.title, item.question, item.answer)
            item.vector_doc_id = doc_id
            item.sync_status = SyncStatus.INDEXING
            db.commit()
            status = wait_document_indexed(dataset_id_for_domain(item.domain.value), doc_id)
            if status != "completed":
                raise DifySyncError(f"indexing not completed: {status}")
            item.sync_status = SyncStatus.COMPLETED
            task.status = SyncTaskStatus.COMPLETED
            db.commit()
            return True

        if task.action == SyncTaskAction.UPDATE:
            if not item.vector_doc_id:
                task.action = SyncTaskAction.CREATE
                db.commit()
                return _execute_task(db, task)
            update_document(item.id, item.domain.value, item.vector_doc_id, item.title, item.question, item.answer)
            item.sync_status = SyncStatus.INDEXING
            db.commit()
            status = wait_document_indexed(dataset_id_for_domain(item.domain.value), item.vector_doc_id)
            if status != "completed":
                raise DifySyncError(f"indexing not completed: {status}")
            item.sync_status = SyncStatus.COMPLETED
            task.status = SyncTaskStatus.COMPLETED
            db.commit()
            return True

        if task.action == SyncTaskAction.DELETE:
            delete_document(item.id, item.domain.value, item.vector_doc_id)
            item.vector_doc_id = None
            item.sync_status = SyncStatus.NOT_SYNCED
            task.status = SyncTaskStatus.COMPLETED
            db.commit()
            return True

        raise DifySyncError(f"unknown task action: {task.action}")
    except DifyDocumentNotFound:
        # Dify 侧文档不存在
        if task.action == SyncTaskAction.DELETE:
            item.vector_doc_id = None
            item.sync_status = SyncStatus.NOT_SYNCED
            task.status = SyncTaskStatus.COMPLETED
        else:
            # 文档被外部删除 -> 清引用，下轮按 CREATE 重建
            item.vector_doc_id = None
            item.sync_status = SyncStatus.NOT_SYNCED
            task.status = SyncTaskStatus.FAILED
            task.last_error = "Dify document missing, will recreate on next run"
        db.commit()
        return True
    except DifySyncError as exc:
        structured_log(
            event="dify_sync_task_failed",
            item_id=item.id,
            domain=item.domain.value,
            status="FAILED",
            error_msg=str(exc),
            extra={"task_id": task.id, "action": task.action.value, "attempts": task.attempts},
        )
        _mark_item_error(db, item, str(exc), task)
        return False


def process_sync_tasks(db: Optional[Session] = None, limit: int = 10) -> int:
    """消费待执行任务（重放入口：脚本/定时任务调用，处理进程退出遗留的 QUEUED/FAILED 任务）。"""
    close_db = db is None
    if db is None:
        db = SessionLocal()
    done = 0
    try:
        tasks = (
            db.query(SyncTask)
            .filter(SyncTask.status == SyncTaskStatus.QUEUED)
            .filter((SyncTask.next_retry_at.is_(None)) | (SyncTask.next_retry_at <= datetime.now()))
            .order_by(SyncTask.id.asc())
            .limit(limit)
            .all()
        )
        for task in tasks:
            _execute_task(db, task)
            done += 1
    finally:
        if close_db:
            db.close()
    return done


def sync_approved_knowledge(item_id: int, db: Optional[Session] = None) -> None:
    """审核通过后的入口（router BackgroundTasks 调用，签名兼容旧版）。

    注册对账任务并立即内联消费；任务持久化，进程重启后由 process_sync_tasks 重放。
    """
    close_db = db is None
    if db is None:
        db = SessionLocal()
    try:
        item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
        if not item or item.status != KnowledgeStatus.APPROVED:
            return
        task = enqueue_sync_task(db, item, SyncTaskAction.CREATE)
        _execute_task(db, task)
    except Exception as exc:  # noqa: BLE001 后台线程兜底：失败仅记日志，任务保留待重试
        import traceback

        traceback.print_exc()
        structured_log(
            event="dify_sync_background_error",
            item_id=item_id,
            status="FAILED",
            error_msg=str(exc),
        )
    finally:
        if close_db:
            db.close()


def sync_removed_knowledge(item_id: int, db: Optional[Session] = None) -> None:
    """驳回/下线后的删除同步入口（router BackgroundTasks 调用，签名与审核入口一致）。

    注册 DELETE 对账任务并立即内联消费；Dify 侧 404 视为已删除，清本地引用即可。
    """
    close_db = db is None
    if db is None:
        db = SessionLocal()
    try:
        item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
        if not item or not item.vector_doc_id:
            return  # 从未同步过，无需删除
        if item.status not in (KnowledgeStatus.REJECTED, KnowledgeStatus.REVOKED):
            return
        task = enqueue_sync_task(db, item, SyncTaskAction.DELETE)
        _execute_task(db, task)
    except Exception as exc:  # noqa: BLE001 后台线程兜底：失败仅记日志，任务保留待重试
        import traceback

        traceback.print_exc()
        structured_log(
            event="dify_delete_background_error",
            item_id=item_id,
            status="FAILED",
            error_msg=str(exc),
        )
    finally:
        if close_db:
            db.close()

"""Dify Dataset Service API 客户端（契约见 docs/DIFY-API-CONTRACT.md，Dify 1.16.1）

职责（写入端）：
- 知识文档的创建（create_by_text）/ 更新 / 删除 / 状态查询；
- payload 契约按 1.16.1 源码冻结：doc_form 枚举 text_model|hierarchical_model|qa_model，
  indexing_technique high_quality|economy；retrieval_mode 不是 create_by_text 字段（删）。
- 同步为异步索引：HTTP 200 仅代表已接收，索引状态需轮询 get_document_status。
"""
from typing import Any, Dict, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import structured_log

settings = get_settings()


class DifySyncError(Exception):
    pass


class DifyDocumentNotFound(DifySyncError):
    """Dify 侧文档不存在（404），调用方按"已删除"处理"""


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.dify_api_key}",
        "Content-Type": "application/json",
    }


def dataset_id_for_domain(domain: str, doc_form: Optional[str] = None) -> str:
    """域（+文档形态）-> 知识库 ID 映射。

    - CS + qa_model（默认）：CS_DATASET_ID（问答库）
    - CS + text_model/hierarchical_model：CS_DOC_DATASET_ID（长文档库，双库预留；
      未配置时回落问答库，保证单库阶段不中断）
    - SALES：SALES_DATASET_ID
    - DOCTOR：DOCTOR_DATASET_ID（Phase 3 预留；未配置时拒绝，不再混入 CS 库）
    """
    if domain == "CS":
        if doc_form in ("text_model", "hierarchical_model") and settings.cs_doc_dataset_id:
            return settings.cs_doc_dataset_id
        return settings.cs_dataset_id
    if domain == "DOCTOR":
        if not settings.doctor_dataset_id:
            raise DifySyncError("DOCTOR_DATASET_ID 未配置（医生域独立知识库预留，未启用）")
        return settings.doctor_dataset_id
    if domain == "SALES":
        return settings.sales_dataset_id
    raise DifySyncError(f"Unsupported domain: {domain}")


def _combined_text(question: Optional[str], answer: str) -> str:
    return f"问题：{question}\n\n答案：{answer}" if question else answer


def _document_payload(
    title: str,
    question: Optional[str],
    answer: str,
    *,
    doc_form: str = "qa_model",
    include_indexing: bool = True,
    process_rule: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """create/update 共用 payload。

    - doc_form：1.16.1 枚举 text_model | hierarchical_model | qa_model（qa 会 400）；
    - retrieval_mode：非 create_by_text 字段（检索方式在创建知识库时配置），不再传；
    - indexing_technique：仅创建时传（update payload 无此字段，传了也会被忽略）。
    """
    payload: Dict[str, Any] = {
        "name": title,
        "text": _combined_text(question, answer),
        "doc_form": doc_form,
        "doc_language": "Chinese",
    }
    if include_indexing:
        payload["indexing_technique"] = "high_quality"
    if process_rule:
        payload["process_rule"] = process_rule
    return payload


def _log(event: str, item_id: int, domain: str, extra: Optional[Dict[str, Any]] = None) -> None:
    structured_log(
        event=event,
        item_id=item_id,
        domain=domain,
        status="SUCCESS",
        extra=extra or {},
    )


def create_document(
    item_id: int,
    domain: str,
    title: str,
    question: Optional[str],
    answer: str,
    doc_form: str = "qa_model",
) -> str:
    """创建文档（Dify 异步索引，调用方需轮询 get_document_status）。返回 document id。"""
    dataset_id = dataset_id_for_domain(domain, doc_form=doc_form)
    payload = _document_payload(title, question, answer, doc_form=doc_form)

    try:
        with httpx.Client(timeout=60.0) as client:
            url = f"{settings.dify_base_url}/datasets/{dataset_id}/document/create_by_text"
            response = client.post(url, headers=_headers(), json=payload)
            response.raise_for_status()
            data = response.json()
            doc_id = data.get("document", {}).get("id") if isinstance(data.get("document"), dict) else data.get("id")
            if not doc_id:
                raise DifySyncError(f"create_by_text 响应缺少 document.id: {response.text[:300]}")
            _log("dify_sync_success", item_id, domain, {"vector_doc_id": doc_id, "dataset_id": dataset_id, "action": "CREATE"})
            return doc_id
    except httpx.HTTPStatusError as exc:
        raise DifySyncError(f"Dify create failed: {exc.response.status_code} {exc.response.text}") from exc


def update_document(
    item_id: int,
    domain: str,
    vector_doc_id: str,
    title: str,
    question: Optional[str],
    answer: str,
) -> None:
    """更新已存在文档（重新触发索引）。"""
    dataset_id = dataset_id_for_domain(domain)
    payload = _document_payload(title, question, answer, include_indexing=False)

    try:
        with httpx.Client(timeout=60.0) as client:
            url = f"{settings.dify_base_url}/datasets/{dataset_id}/documents/{vector_doc_id}"
            response = client.put(url, headers=_headers(), json=payload)
            response.raise_for_status()
            _log("dify_sync_success", item_id, domain, {"vector_doc_id": vector_doc_id, "dataset_id": dataset_id, "action": "UPDATE"})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise DifyDocumentNotFound(f"Dify document not found: {vector_doc_id}") from exc
        raise DifySyncError(f"Dify update failed: {exc.response.status_code} {exc.response.text}") from exc


def delete_document(item_id: int, domain: str, vector_doc_id: str) -> bool:
    """删除 Dify 文档（永久删除文档与全部 chunk）。

    - 404：视为已删除，返回 False（调用方清本地引用即可）；
    - 400 document_indexing：文档索引中不可删，抛 DifySyncError 由调用方延迟重试。
    """
    dataset_id = dataset_id_for_domain(domain)
    try:
        with httpx.Client(timeout=60.0) as client:
            url = f"{settings.dify_base_url}/datasets/{dataset_id}/documents/{vector_doc_id}"
            response = client.delete(url, headers=_headers())
            if response.status_code == 404:
                structured_log(
                    event="dify_delete_missing",
                    item_id=item_id,
                    domain=domain,
                    status="SUCCESS",
                    extra={"vector_doc_id": vector_doc_id, "dataset_id": dataset_id},
                )
                return False
            response.raise_for_status()
            _log("dify_delete_success", item_id, domain, {"vector_doc_id": vector_doc_id, "dataset_id": dataset_id})
            return True
    except httpx.HTTPStatusError as exc:
        raise DifySyncError(f"Dify delete failed: {exc.response.status_code} {exc.response.text}") from exc


def get_document_status(dataset_id: str, vector_doc_id: str) -> Dict[str, Any]:
    """查询文档详情：indexing_status / display_status / error。"""
    try:
        with httpx.Client(timeout=60.0) as client:
            url = f"{settings.dify_base_url}/datasets/{dataset_id}/documents/{vector_doc_id}"
            response = client.get(url, headers=_headers())
            if response.status_code == 404:
                raise DifyDocumentNotFound(f"Dify document not found: {vector_doc_id}")
            response.raise_for_status()
            data = response.json()
            return {
                "indexing_status": data.get("indexing_status"),
                "display_status": data.get("display_status"),
                "error": data.get("error"),
            }
    except httpx.HTTPStatusError as exc:
        raise DifySyncError(f"Dify status query failed: {exc.response.status_code} {exc.response.text}") from exc


def wait_document_indexed(dataset_id: str, vector_doc_id: str, timeout: int = 300, interval: int = 10) -> str:
    """轮询文档索引状态直到 completed/error；超时返回 timeout。"""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = get_document_status(dataset_id, vector_doc_id)
        if status.get("indexing_status") in ("completed", "error"):
            return status.get("indexing_status", "unknown")
        time.sleep(interval)
    return "timeout"


def sync_knowledge_to_dify(
    item_id: int,
    domain: str,
    title: str,
    question: Optional[str],
    answer: str,
    tags: Optional[Any] = None,
    vector_doc_id: Optional[str] = None,
) -> Optional[str]:
    """兼容旧调用方：创建或更新文档（见 tests/test_dify_sync.py）。

    tags 参数保留仅为兼容旧签名：1.16.1 create_by_text 无 tags 字段，忽略。
    """
    if vector_doc_id:
        update_document(item_id, domain, vector_doc_id, title, question, answer)
        return vector_doc_id
    return create_document(item_id, domain, title, question, answer)

from typing import Any, Dict, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import structured_log

settings = get_settings()


class DifySyncError(Exception):
    pass


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.dify_api_key}",
        "Content-Type": "application/json",
    }


def dataset_id_for_domain(domain: str) -> str:
    if domain == "CS" or domain == "DOCTOR":
        return settings.cs_dataset_id
    if domain == "SALES":
        return settings.sales_dataset_id
    raise DifySyncError(f"Unsupported domain: {domain}")


def _build_document_payload(item_id: int, title: str, question: Optional[str], answer: str, tags: Optional[Any]) -> Dict[str, Any]:
    # Dify Document by text: name + text
    combined_text = f"问题：{question}\n\n答案：{answer}" if question else answer
    return {
        "name": title,
        "text": combined_text,
        "indexing_technique": "high_quality",
        "doc_form": "qa",
        "doc_language": "Chinese",
        "retrieval_mode": "semantic",
        "tags": tags or [],
    }


def sync_knowledge_to_dify(
    item_id: int,
    domain: str,
    title: str,
    question: Optional[str],
    answer: str,
    tags: Optional[Any] = None,
    vector_doc_id: Optional[str] = None,
) -> Optional[str]:
    dataset_id = dataset_id_for_domain(domain)
    payload = _build_document_payload(item_id, title, question, answer, tags)

    try:
        with httpx.Client(timeout=60.0) as client:
            if vector_doc_id:
                url = f"{settings.dify_base_url}/datasets/{dataset_id}/documents/{vector_doc_id}"
                response = client.put(url, headers=_headers(), json=payload)
            else:
                url = f"{settings.dify_base_url}/datasets/{dataset_id}/document/create_by_text"
                response = client.post(url, headers=_headers(), json=payload)

            response.raise_for_status()
            data = response.json()

            # Dify create_by_text returns document under "document" or batch id
            doc_id = data.get("document", {}).get("id") if isinstance(data.get("document"), dict) else data.get("id")

            structured_log(
                event="dify_sync_success",
                item_id=item_id,
                domain=domain,
                status="SUCCESS",
                extra={"vector_doc_id": doc_id, "dataset_id": dataset_id},
            )
            return doc_id

    except httpx.HTTPStatusError as exc:
        structured_log(
            event="dify_sync_failed",
            item_id=item_id,
            domain=domain,
            status="FAILED",
            error_msg=f"HTTP {exc.response.status_code}: {exc.response.text}",
            extra={"dataset_id": dataset_id},
        )
        raise DifySyncError(f"Dify sync failed: {exc.response.status_code} {exc.response.text}")

    except Exception as exc:  # noqa: BLE001
        structured_log(
            event="dify_sync_failed",
            item_id=item_id,
            domain=domain,
            status="FAILED",
            error_msg=str(exc),
            extra={"dataset_id": dataset_id},
        )
        raise DifySyncError(f"Dify sync failed: {exc}")

"""Dify 知识库发布客户端（Service API 契约见 docs/DIFY-API-CONTRACT.md，Dify 1.16.1）。

职责（写入端）：
- ``create_by_file``：直传原始文件到 Dataset，Dify 原生切割（doc_form=text_model/hierarchical_model），
  CS_DOC 长文档库（general chunk）走此通道；
- Pipeline 驱动：上传临时文件 → ``/datasets/{id}/pipeline/run`` 运行该知识库已发布的
  Knowledge Pipeline（FILE -> QA Processor -> KB，qa_model 问答库走此通道）；
- 公共能力：索引状态轮询 / segments 回读 / 文档删除（404 视为已删）。

数据库是事实源，Dify 是可重建投影。本文件是 CS 文档发布两条路径（直传 / Pipeline）
共用的唯一 Dify 客户端；旧 ``dify_client.py``（create_by_text 文本写入）随旧知识项链路
（core_knowledge_items）下线而删除。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx

from app.core.config import get_settings


class DifySyncError(Exception):
    pass


class DifyDocumentNotFound(DifySyncError):
    """Dify 侧文档不存在（404），调用方按"已删除"处理"""


class DifyPipelineError(DifySyncError):
    """Dify 发布（Pipeline 运行 / create_by_file 直传）失败。"""


def _settings():
    return get_settings()


def _api_key() -> str:
    settings = _settings()
    key = settings.dify_knowledge_api_key or settings.dify_api_key
    if not key:
        raise DifyPipelineError("DIFY_KNOWLEDGE_API_KEY is not configured")
    return key


def _headers(json: bool = True) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {_api_key()}"}
    if json:
        headers["Content-Type"] = "application/json"
    return headers


def _base_url() -> str:
    return _settings().dify_base_url.rstrip("/")


import re


def sanitize_dify_filename(filename: str) -> str:
    """清理文件名中 Dify 不支持的非法字符（如斜杠、冒号、换行等），确保符合 Dify 契约。"""
    base = Path(filename).name
    # 替换非法字符: \ / : * ? " < > | \r \n \t
    clean = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', base).strip(' ._')
    if not clean:
        clean = "document.md"
    return clean


# ============ 发布路径一：create_by_file 直传（text_model/hierarchical_model） ============


def create_by_file(
    dataset_id: str,
    file_path: Path,
    filename: str,
    doc_form: str = "text_model",
    process_rule: Optional[dict[str, Any]] = None,
) -> str:
    """直传原始文件到 Dataset，Dify 原生切割后异步索引，返回 document id。

    ``POST /datasets/{dataset_id}/document/create_by_file``（multipart）：
    - ``file`` 字段：原始文件二进制；
    - ``data`` 字段：JSON 字符串（name / doc_form / indexing_technique / process_rule / doc_language 等）。
    实测（Dify 1.16.1）：``process_rule`` 必填，缺失返回 400 invalid_param "process_rule is required."，
    默认使用 automatic 切分（``{"mode": "automatic"}``），可传入 custom 覆盖。
    返回 200 仅代表已接收，调用方需轮询索引状态（wait_document_indexed）。
    """
    safe_filename = sanitize_dify_filename(filename)
    payload: dict[str, Any] = {
        "name": safe_filename,
        "doc_form": doc_form,
        "doc_language": "Chinese",
        "indexing_technique": "high_quality",
        # 1.16.1 create_by_file 强制要求 process_rule；automatic = Dify 默认切分规则
        "process_rule": process_rule or {"mode": "automatic"},
    }
    try:
        with file_path.open("rb") as source, httpx.Client(trust_env=False, timeout=120.0) as client:
            response = client.post(
                f"{_base_url()}/datasets/{dataset_id}/document/create_by_file",
                headers=_headers(json=False),
                files={"file": (safe_filename, source)},
                data={"data": json.dumps(payload, ensure_ascii=False)},
            )
            response.raise_for_status()
            data = response.json()
            doc_id = data.get("document", {}).get("id") if isinstance(data.get("document"), dict) else data.get("id")
            if not doc_id:
                raise DifyPipelineError(f"create_by_file 响应缺少 document.id: {response.text[:300]}")
            return str(doc_id)
    except httpx.HTTPStatusError as exc:
        raise DifyPipelineError(f"Dify create_by_file failed: {exc.response.status_code} {exc.response.text}") from exc


# ============ 发布路径二：Knowledge Pipeline 驱动（qa_model 问答库） ============


def list_pipeline_datasources(dataset_id: str, is_published: bool = True) -> list[dict[str, Any]]:
    """Return published datasource nodes; used to discover the FILE node id."""
    try:
        with httpx.Client(trust_env=False, timeout=60.0) as client:
            response = client.get(
                f"{_base_url()}/datasets/{dataset_id}/pipeline/datasource-plugins",
                headers=_headers(json=False),
                params={"is_published": str(is_published).lower()},
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise DifyPipelineError("Unexpected datasource-plugins response")
            return data
    except httpx.HTTPStatusError as exc:
        raise DifyPipelineError(f"Dify datasource lookup failed: {exc.response.status_code} {exc.response.text}") from exc


def resolve_local_file_node_id(dataset_id: str) -> str:
    """Resolve the only local_file datasource node, otherwise demand explicit config."""
    configured = _settings().cs_pipeline_file_node_id
    if configured:
        return configured
    matches = [node for node in list_pipeline_datasources(dataset_id) if node.get("datasource_type") == "local_file"]
    if len(matches) != 1 or not matches[0].get("node_id"):
        raise DifyPipelineError(
            "Expected exactly one local_file datasource; set CS_PIPELINE_FILE_NODE_ID explicitly"
        )
    return str(matches[0]["node_id"])


def upload_pipeline_file(file_path: Path, filename: Optional[str] = None) -> dict[str, Any]:
    """Upload a local source file and return Dify's transient pipeline-file object."""
    safe_filename = sanitize_dify_filename(filename or file_path.name)
    try:
        with file_path.open("rb") as source, httpx.Client(trust_env=False, timeout=120.0) as client:
            response = client.post(
                f"{_base_url()}/datasets/pipeline/file-upload",
                headers=_headers(json=False),
                files={"file": (safe_filename, source)},
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("id"):
                raise DifyPipelineError("Dify pipeline file upload response has no id")
            return payload
    except httpx.HTTPStatusError as exc:
        raise DifyPipelineError(f"Dify pipeline file upload failed: {exc.response.status_code} {exc.response.text}") from exc


def run_pipeline(
    dataset_id: str,
    file_id: str,
    filename: str,
    start_node_id: str,
    *,
    inputs: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Run the *published* local-file Knowledge Pipeline in blocking mode.

    The worker invokes this function, never a request/WeChat callback. Dify's
    response is persisted intact because node-output keys differ by Dify version
    and Pipeline configuration.
    """
    safe_filename = sanitize_dify_filename(filename)
    body = {
        "inputs": inputs or {},
        "datasource_type": "local_file",
        "datasource_info_list": [{"reference": file_id, "name": safe_filename}],
        "start_node_id": start_node_id,
        "is_published": True,
        "response_mode": "blocking",
    }
    try:
        with httpx.Client(trust_env=False, timeout=float(_settings().dify_pipeline_timeout_seconds)) as client:
            response = client.post(
                f"{_base_url()}/datasets/{dataset_id}/pipeline/run",
                headers=_headers(),
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise DifyPipelineError("Unexpected pipeline run response")
            if data.get("status") in {"failed", "error"}:
                raise DifyPipelineError(str(data.get("error") or data))
            return data
    except httpx.HTTPStatusError as exc:
        raise DifyPipelineError(f"Dify pipeline run failed: {exc.response.status_code} {exc.response.text}") from exc


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def extract_pipeline_identifiers(run_output: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Best-effort extraction of a run and Dify document id from persisted output.

    The Knowledge Base node's exact output name is plugin/version dependent, so
    we preserve raw output and accept several documented/common keys. A missing
    document id is a publish failure: it must be fixed in the Pipeline output,
    not guessed from a filename.
    """
    run_id = run_output.get("workflow_run_id") or run_output.get("pipeline_run_id") or run_output.get("task_id")
    document_keys = ("document_id", "dify_document_id", "knowledge_document_id")
    document_id: Optional[str] = None
    for node in _walk(run_output):
        for key in document_keys:
            value = node.get(key)
            if isinstance(value, str) and value:
                document_id = value
                break
        if document_id:
            break
        documents = node.get("documents")
        if isinstance(documents, list) and documents and isinstance(documents[0], dict):
            value = documents[0].get("id") or documents[0].get("document_id")
            if isinstance(value, str) and value:
                document_id = value
                break
    return (str(run_id) if run_id else None, document_id)


# ============ 公共能力：轮询 / segments 回读 / 删除（与发布方式无关） ============


def wait_pipeline_document_indexed(dataset_id: str, document_id: str) -> str:
    """Reuse the standard Dataset document status API for a Pipeline output."""
    settings = _settings()
    import time

    deadline = time.monotonic() + settings.dify_pipeline_timeout_seconds
    while time.monotonic() < deadline:
        with httpx.Client(trust_env=False, timeout=60.0) as client:
            response = client.get(
                f"{_base_url()}/datasets/{dataset_id}/documents/{document_id}",
                headers=_headers(json=False),
            )
            if response.status_code == 404:
                raise DifyDocumentNotFound(f"Dify document not found: {document_id}")
            response.raise_for_status()
            doc_json = response.json()
            status = doc_json.get("indexing_status")
        if status == "completed":
            return "completed"
        if status == "error":
            dify_err = doc_json.get("error") or doc_json.get("display_status") or "indexing error in Dify"
            raise DifyPipelineError(f"Dify indexing error: {dify_err}")
        time.sleep(5)
    return "timeout"


def list_document_segments(dataset_id: str, document_id: str) -> list[dict[str, Any]]:
    """Read processed QA/text blocks back from Dify for immutable local snapshots."""
    segments: list[dict[str, Any]] = []
    page = 1
    try:
        with httpx.Client(trust_env=False, timeout=60.0) as client:
            while True:
                response = client.get(
                    f"{_base_url()}/datasets/{dataset_id}/documents/{document_id}/segments",
                    headers=_headers(json=False),
                    params={"page": page, "limit": 100},
                )
                if response.status_code == 404:
                    raise DifyDocumentNotFound(f"Dify document not found: {document_id}")
                response.raise_for_status()
                payload = response.json()
                batch = payload.get("data", [])
                if not isinstance(batch, list):
                    raise DifyPipelineError("Unexpected Dify segments response")
                segments.extend(item for item in batch if isinstance(item, dict))
                if not payload.get("has_more"):
                    return segments
                page += 1
    except httpx.HTTPStatusError as exc:
        raise DifyPipelineError(f"Dify segment listing failed: {exc.response.status_code} {exc.response.text}") from exc


def delete_pipeline_document(dataset_id: str, document_id: str) -> None:
    """Delete a document created by the Pipeline; 404 is already-revoked success."""
    try:
        with httpx.Client(trust_env=False, timeout=60.0) as client:
            response = client.delete(
                f"{_base_url()}/datasets/{dataset_id}/documents/{document_id}",
                headers=_headers(json=False),
            )
            if response.status_code == 404:
                return
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise DifyPipelineError(f"Dify document delete failed: {exc.response.status_code} {exc.response.text}") from exc

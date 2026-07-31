import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch

import httpx

from app.services.dify_sync import DifySyncError, sync_knowledge_to_dify


def test_sync_creates_document_when_no_vector_doc_id():
    with patch("httpx.Client") as mock_client_class:
        mock_response = mock_client_class.return_value.__enter__.return_value.post.return_value
        mock_response.json.return_value = {"document": {"id": "doc-123"}}
        mock_response.raise_for_status = lambda: None

        doc_id = sync_knowledge_to_dify(
            item_id=1,
            domain="CS",
            title="测试",
            question="问题",
            answer="答案",
            tags=["测试"],
            vector_doc_id=None,
        )
        assert doc_id == "doc-123"


def test_sync_updates_document_when_vector_doc_id_exists():
    with patch("httpx.Client") as mock_client_class:
        mock_response = mock_client_class.return_value.__enter__.return_value.put.return_value
        mock_response.json.return_value = {"document": {"id": "doc-456"}}
        mock_response.raise_for_status = lambda: None

        doc_id = sync_knowledge_to_dify(
            item_id=2,
            domain="SALES",
            title="销售测试",
            question=None,
            answer="答案",
            tags=["销售"],
            vector_doc_id="doc-456",
        )
        assert doc_id == "doc-456"


def test_sync_raises_on_http_error():
    with patch("httpx.Client") as mock_client_class:
        mock_response = mock_client_class.return_value.__enter__.return_value.post.return_value
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request", request=None, response=type("R", (), {"status_code": 400, "text": "bad"})()
        )

        try:
            sync_knowledge_to_dify(
                item_id=3,
                domain="CS",
                title="测试",
                question="问题",
                answer="答案",
            )
            assert False, "Should raise DifySyncError"
        except DifySyncError:
            pass


def test_unsupported_domain():
    try:
        sync_knowledge_to_dify(
            item_id=4,
            domain="UNKNOWN",
            title="测试",
            question="问题",
            answer="答案",
        )
        assert False, "Should raise DifySyncError"
    except DifySyncError as exc:
        assert "Unsupported domain" in str(exc)

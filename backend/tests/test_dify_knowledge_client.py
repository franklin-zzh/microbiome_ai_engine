from unittest.mock import patch

from app.clients.dify_knowledge_client import create_by_file, extract_pipeline_identifiers, run_pipeline


def test_pipeline_run_uses_local_file_contract(monkeypatch):
    monkeypatch.setattr("app.clients.dify_knowledge_client._base_url", lambda: "http://dify/v1")
    monkeypatch.setattr("app.clients.dify_knowledge_client._headers", lambda json=True: {"Authorization": "Bearer test"})
    monkeypatch.setattr("app.clients.dify_knowledge_client._settings", lambda: type("S", (), {"dify_pipeline_timeout_seconds": 10})())

    with patch("httpx.Client") as client_class:
        response = client_class.return_value.__enter__.return_value.post.return_value
        response.raise_for_status = lambda: None
        response.json.return_value = {"workflow_run_id": "run-1", "outputs": {"document_id": "doc-1"}}

        result = run_pipeline("dataset-1", "file-1", "faq.pdf", "file-node-1")

        assert result["workflow_run_id"] == "run-1"
        kwargs = client_class.return_value.__enter__.return_value.post.call_args.kwargs
        assert kwargs["json"]["datasource_type"] == "local_file"
        assert kwargs["json"]["datasource_info_list"] == [{"reference": "file-1", "name": "faq.pdf"}]
        assert kwargs["json"]["start_node_id"] == "file-node-1"
        assert kwargs["json"]["is_published"] is True


def test_pipeline_identifier_extraction_reads_knowledge_base_output():
    run_id, document_id = extract_pipeline_identifiers(
        {"workflow_run_id": "run-2", "data": {"nodes": [{"outputs": {"documents": [{"id": "doc-2"}]}}]}}
    )
    assert run_id == "run-2"
    assert document_id == "doc-2"


def test_create_by_file_direct_upload_contract(tmp_path, monkeypatch):
    """create_by_file 直传：multipart + data JSON 字符串，返回 document.id。"""
    monkeypatch.setattr("app.clients.dify_knowledge_client._base_url", lambda: "http://dify/v1")
    monkeypatch.setattr("app.clients.dify_knowledge_client._headers", lambda json=True: {"Authorization": "Bearer test"})

    source = tmp_path / "company_manual.md"
    source.write_text("# 公司手册\n## 第一章\n内容", encoding="utf-8")

    with patch("httpx.Client") as client_class:
        response = client_class.return_value.__enter__.return_value.post.return_value
        response.raise_for_status = lambda: None
        response.json.return_value = {"document": {"id": "doc-file-1"}, "batch": "b1"}

        doc_id = create_by_file("dataset-doc", source, "company_manual.md", doc_form="text_model")

        assert doc_id == "doc-file-1"
        call = client_class.return_value.__enter__.return_value.post.call_args
        assert call.args[0] == "http://dify/v1/datasets/dataset-doc/document/create_by_file"
        assert call.kwargs["files"]["file"][0] == "company_manual.md"
        import json

        data = json.loads(call.kwargs["data"]["data"])
        assert data["doc_form"] == "text_model"
        assert data["indexing_technique"] == "high_quality"
        assert data["name"] == "company_manual.md"
        # 1.16.1 create_by_file 强制要求 process_rule（缺失 400），默认 automatic
        assert data["process_rule"] == {"mode": "automatic"}


def test_create_by_file_missing_document_id_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("app.clients.dify_knowledge_client._base_url", lambda: "http://dify/v1")
    monkeypatch.setattr("app.clients.dify_knowledge_client._headers", lambda json=True: {"Authorization": "Bearer test"})

    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")

    with patch("httpx.Client") as client_class:
        response = client_class.return_value.__enter__.return_value.post.return_value
        response.raise_for_status = lambda: None
        response.json.return_value = {"batch": "b1"}

        from app.clients.dify_knowledge_client import DifyPipelineError

        try:
            create_by_file("dataset-doc", source, "a.txt")
            raise AssertionError("expected DifyPipelineError")
        except DifyPipelineError as exc:
            assert "document.id" in str(exc)

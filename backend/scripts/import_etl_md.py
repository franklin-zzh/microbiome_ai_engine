"""批量入库：把 ETL 清洗产出的 md 文件，经后端「文档→版本→审核」链路发布到 Dify 知识库。

复用已有 pipeline（不新建通道）：
- text_model / hierarchical_model → create_by_file 直传 CS_DOC（Dify 显示名 cs_general）
- qa_model → Knowledge Pipeline 生成 QA → CS_QA（Dify 显示名 cs_qa）

链路：登录 → 上传 asset → 建文档/版本 → approve（触发 publish_document_version 后台任务）
     → 轮询发布状态（COMPLETED / FAILED / timeout）

用法（backend 服务需在 http://localhost:8000 运行，.env 需有 ADMIN_USERNAME/ADMIN_PASSWORD）：
    cd backend && .venv\\Scripts\\python.exe scripts\\import_etl_md.py --md-dir ..\\etl\\out\\mixed_md
    # 同名文档已存在时上传新版本并审核：
    ... --update
    # 只上传不审核（人工在审核后台过审）：
    ... --no-publish
"""
import argparse
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ROOT_ENV = BACKEND_ROOT.parent / ".env"
load_dotenv(ROOT_ENV)

DEFAULT_BASE = "http://localhost:8000/api/v1"


def login(client: httpx.Client, base: str, username: str, password: str) -> str:
    resp = client.post(f"{base}/auth/login", json={"username": username, "password": password})
    resp.raise_for_status()
    return resp.json()["access_token"]


def list_documents(client: httpx.Client, base: str, token: str) -> dict[str, int]:
    """title -> document_id（分页拉全量，仅用于 --update 查重）"""
    headers = {"Authorization": f"Bearer {token}"}
    mapping: dict[str, int] = {}
    page, limit = 1, 100
    while True:
        resp = client.get(
            f"{base}/admin/knowledge/documents",
            headers=headers,
            params={"limit": limit, "skip": (page - 1) * limit},
        )
        resp.raise_for_status()
        payload = resp.json()
        for item in payload.get("items", []):
            mapping[item["title"]] = item["id"]
        if len(payload.get("items", [])) < limit or payload.get("total", 0) <= page * limit:
            break
        page += 1
    return mapping


def upload_asset(client: httpx.Client, base: str, token: str, md_path: Path) -> int:
    headers = {"Authorization": f"Bearer {token}"}
    with md_path.open("rb") as f:
        resp = client.post(
            f"{base}/knowledge/documents/assets",
            headers=headers,
            files={"file": (md_path.name, f, "text/markdown")},
        )
    resp.raise_for_status()
    return resp.json()["id"]


def create_document(
    client: httpx.Client, base: str, token: str, asset_id: int, title: str, category: str, doc_form: str, created_by: str
) -> tuple[int, int]:
    """返回 (document_id, version_id)"""
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "asset_id": asset_id,
        "title": title,
        "category": category,
        "doc_form": doc_form,
        "tags": [],
        "created_by": created_by,
    }
    resp = client.post(f"{base}/knowledge/documents", headers=headers, json=body)
    resp.raise_for_status()
    doc = resp.json()
    version_id = doc["versions"][0]["id"]
    return doc["id"], version_id


def create_version(client: httpx.Client, base: str, token: str, document_id: int, asset_id: int, title: str, created_by: str) -> int:
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.post(
        f"{base}/admin/knowledge/documents/{document_id}/versions",
        headers=headers,
        json={"asset_id": asset_id, "title": title, "created_by": created_by},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def approve(client: httpx.Client, base: str, token: str, version_id: int, operator: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.post(
        f"{base}/admin/knowledge/document-versions/{version_id}/approve",
        headers=headers,
        json={"operator": operator, "review_note": "ETL 批量入库自动审核"},
    )
    resp.raise_for_status()


def poll_publish(client: httpx.Client, base: str, token: str, document_id: int, timeout: int, interval: int) -> tuple[str, str]:
    """轮询最新版本的发布状态，返回 (status, last_error)。"""
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"{base}/admin/knowledge/documents/{document_id}", headers=headers)
        resp.raise_for_status()
        doc = resp.json()
        versions = doc.get("versions") or []
        if versions:
            targets = versions[-1].get("publish_targets") or []
            if targets:
                status = targets[0].get("status", "")
                if status in {"COMPLETED", "FAILED", "DELETED"}:
                    return status, targets[0].get("last_error") or ""
        time.sleep(interval)
    return "TIMEOUT", ""


def revoke_document(client: httpx.Client, base: str, token: str, document_id: int, operator: str) -> None:
    """撤下文档最新已发布版本（删除 Dify 投影 + 本地归档）。"""
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get(f"{base}/admin/knowledge/documents/{document_id}", headers=headers)
    resp.raise_for_status()
    versions = resp.json().get("versions") or []
    target = next((v for v in versions if v.get("status") == "PUBLISHED"), None)
    if not target:
        raise RuntimeError(f"document={document_id} 无 PUBLISHED 版本可撤下")
    resp = client.post(
        f"{base}/admin/knowledge/document-versions/{target['id']}/revoke",
        headers=headers,
        json={"operator": operator, "review_note": "ETL 批量清理"},
    )
    resp.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL md → backend 审核链路 → Dify 知识库（复用已有 pipeline）")
    parser.add_argument("--md-dir", required=True, help="ETL 输出目录（含 *.md）")
    parser.add_argument("--doc-form", default="text_model", choices=["text_model", "hierarchical_model", "qa_model"])
    parser.add_argument("--category", default="GENERAL")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help="backend API 前缀（默认本机 8000）")
    parser.add_argument("--operator", default="", help="审核人（默认 ADMIN_USERNAME）")
    parser.add_argument("--update", action="store_true", help="同名文档已存在时上传新版本并审核（默认跳过）")
    parser.add_argument("--no-publish", action="store_true", help="只上传+建文档/版本，不自动审核（人工后台审）")
    parser.add_argument("--revoke", action="store_true", help="清理模式：撤下 md-dir 中同名文档（删除 Dify 投影）",)
    parser.add_argument("--timeout", type=int, default=600, help="发布轮询超时秒数")
    parser.add_argument("--poll-interval", type=int, default=5)
    args = parser.parse_args()

    username = os.getenv("ADMIN_USERNAME", "")
    password = os.getenv("ADMIN_PASSWORD", "")
    if not username or not password:
        print("SKIPPED: .env 缺少 ADMIN_USERNAME / ADMIN_PASSWORD，无法登录后端。")
        return 0
    operator = args.operator or username

    md_dir = Path(args.md_dir)
    md_files = sorted(p for p in md_dir.glob("*.md") if p.is_file())
    if not md_files:
        print(f"未找到 md 文件: {md_dir}")
        return 1

    with httpx.Client(timeout=120.0, trust_env=False) as client:
        try:
            token = login(client, args.base_url, username, password)
        except httpx.HTTPError as exc:
            print(f"[ERROR] 后端登录失败（{args.base_url}）: {exc}")
            return 1
        existing = list_documents(client, args.base_url, token)
        if args.revoke:
            ok = failed = 0
            for md in md_files:
                document_id = existing.get(md.stem)
                if not document_id:
                    print(f"[SKIP] 无此文档: {md.stem}")
                    continue
                try:
                    revoke_document(client, args.base_url, token, document_id, operator)
                    print(f"[REVOKED] {md.stem} -> document={document_id}")
                    ok += 1
                except Exception as exc:
                    print(f"[FAIL] {md.stem} -> {exc}")
                    failed += 1
            print(f"\n汇总: REVOKED={ok} FAIL={failed}")
            return 1 if failed else 0
        print(f"登录成功；已存在 {len(existing)} 个文档。共 {len(md_files)} 个 md 待入库（doc_form={args.doc_form}）")
        ok = skip = failed = 0
        for md in md_files:
            title = md.stem
            document_id = existing.get(title)
            if document_id and not args.update:
                print(f"[SKIP] 已存在同名文档: {title}（--update 可更新）")
                skip += 1
                continue
            try:
                asset_id = upload_asset(client, args.base_url, token, md)
                if document_id and args.update:
                    version_id = create_version(client, args.base_url, token, document_id, asset_id, title, operator)
                else:
                    document_id, version_id = create_document(
                        client, args.base_url, token, asset_id, title, args.category, args.doc_form, operator
                    )
                if args.no_publish:
                    print(f"[UPLOADED] {title} -> document={document_id} version={version_id}（待人工审核）")
                    ok += 1
                    continue
                approve(client, args.base_url, token, version_id, operator)
                status, last_error = poll_publish(client, args.base_url, token, document_id, args.timeout, args.poll_interval)
                if status == "COMPLETED":
                    print(f"[OK] {title} -> document={document_id} version={version_id} PUBLISHED")
                    ok += 1
                else:
                    print(f"[FAIL] {title} -> publish={status} {last_error}")
                    failed += 1
            except httpx.HTTPStatusError as exc:
                print(f"[FAIL] {title} -> HTTP {exc.response.status_code} {exc.response.text[:200]}")
                failed += 1
        print(f"\n汇总: OK={ok} SKIP={skip} FAIL={failed}")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

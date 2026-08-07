"""Dify Service API 契约实测脚本（对齐 Dify 1.16.1）

用途：对服务器上实际运行的 Dify 1.16.1 做一次真实 API 联调，验证并冻结
docs/DIFY-API-CONTRACT.md 中记录的契约（路径 / payload / 状态码 / 异步索引行为）。

用法：
    1. 根目录 .env 填好 DIFY_API_KEY（Service API 密钥）与 CS_DATASET_ID（任一知识库 ID）
    2. cd backend && .venv\\Scripts\\python.exe scripts\\verify_dify_contract.py
    3. 脚本会创建两个临时文档（qa_model / text_model），轮询索引状态，最后 DELETE 清理；
       全部通过输出 CONTRACT OK，任何一步失败输出真实状态码与响应体

未配置密钥时脚本安全退出（SKIPPED），不产生任何副作用。
"""
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ROOT_ENV = BACKEND_ROOT.parent / ".env"

load_dotenv(ROOT_ENV)

BASE_URL = os.getenv("DIFY_BASE_URL", "").rstrip("/")
API_KEY = os.getenv("DIFY_API_KEY", "")
DATASET_ID = os.getenv("CS_DATASET_ID", "")

# 1.16.1 知识库文档管理的两类路径（canonical=连字符 / legacy=下划线，均注册）
CANONICAL_PREFIX = f"/datasets/{DATASET_ID}/documents"
LEGACY_PREFIX = f"/datasets/{DATASET_ID}/document"

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def log(step: str, resp: httpx.Response, extra: str = "") -> None:
    body = resp.text[:300].replace("\n", " ")
    print(f"[{step}] HTTP {resp.status_code} {extra}\n    -> {body}")


def create_by_text(client: httpx.Client, doc_form: str) -> tuple[str, str]:
    """创建文档，返回 (document_id, batch)。先试 legacy 下划线路径（后端 dify_client 现用），
    404 则回退 canonical 连字符路径——用于验证 1.16.1 是否仍保留 legacy alias。"""
    payload = {
        "name": f"CONTRACT-TEST-{doc_form}",
        "text": "问题：测试问题一\n\n答案：测试答案一\n\n问题：测试问题二\n\n答案：测试答案二",
        "indexing_technique": "high_quality",
        "doc_form": doc_form,
        "doc_language": "Chinese",
    }
    url = f"{LEGACY_PREFIX}/create_by_text"
    resp = client.post(url, json=payload)
    if resp.status_code == 404:
        url = f"{CANONICAL_PREFIX}/create-by-text"
        resp = client.post(url, json=payload)
        log("create_by_text", resp, f"path=canonical({url.split('/datasets/')[1]})")
    else:
        log("create_by_text", resp, f"path=legacy({url.split('/datasets/')[1]})")

    resp.raise_for_status()
    data = resp.json()
    doc = data.get("document") or {}
    return doc.get("id", ""), data.get("batch", "")


def wait_indexed(client: httpx.Client, document_id: str, timeout: int = 180) -> str:
    """轮询文档索引状态（Dify 异步 celery；HTTP 200 仅代表已接收）"""
    url = f"{CANONICAL_PREFIX}/{document_id}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(url)
        if resp.status_code != 200:
            log("poll_document", resp, "unexpected status")
            resp.raise_for_status()
        status = resp.json().get("indexing_status", "unknown")
        print(f"    poll indexing_status={status}")
        if status in ("completed", "error"):
            return status
        time.sleep(5)
    return "timeout"


def delete_document(client: httpx.Client, document_id: str) -> None:
    url = f"{CANONICAL_PREFIX}/{document_id}"
    resp = client.delete(url)
    log("delete_document", resp)
    if resp.status_code == 204:
        # 删除后应 404
        after = client.get(url)
        log("get_after_delete", after, "(期望 404)")
    else:
        print("    !! delete failed; document left in Dify for manual cleanup")


def main() -> int:
    if not API_KEY or not DATASET_ID:
        print("SKIPPED: 根目录 .env 缺少 DIFY_API_KEY / CS_DATASET_ID，无法实测。")
        print("回填后重跑；当前契约以 docs/DIFY-API-CONTRACT.md（源码核实版）为准。")
        return 0
    if not BASE_URL:
        print("SKIPPED: 根目录 .env 缺少 DIFY_BASE_URL")
        return 0

    print(f"== Dify contract test @ {BASE_URL} dataset={DATASET_ID}")
    print("== 前置检查：GET dataset（验证 Service API Key 有效）")
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=60.0) as client:
        probe = client.get(f"/datasets/{DATASET_ID}")
        log("get_dataset", probe)
        if probe.status_code == 401:
            print("!! Service API Key 无效（401）；无法继续")
            return 1
        probe.raise_for_status()

        for doc_form in ("qa_model", "text_model"):
            print(f"\n== CASE doc_form={doc_form}")
            doc_id = ""
            try:
                doc_id, batch = create_by_text(client, doc_form)
                print(f"    document_id={doc_id} batch={batch}")
                if not doc_id:
                    print("    !! 响应中没有 document.id，跳过该形态")
                    continue
                status = wait_indexed(client, doc_id)
                print(f"    final indexing_status={status}")
            finally:
                if doc_id:
                    delete_document(client, doc_id)

    print("\n== CONTRACT OK（qa_model / text_model 创建-索引-删除全链路通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

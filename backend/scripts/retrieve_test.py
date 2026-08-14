"""Dify 知识库召回测试工具（读取端）。

用法：
    cd backend && .venv\\Scripts\\python.exe scripts\\retrieve_test.py \
        --query "肠道菌群检测的流程是怎样的？多久出报告？"
    # 多条 query、指定库与检索方式：
    ... --query "问题1" --query "问题2" --dataset CS_DOC --method hybrid_search --top-k 5

读取端与写入端解耦：直接调 Dify Service API（POST /datasets/{id}/retrieve）。
"""
import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ROOT_ENV = BACKEND_ROOT.parent / ".env"
load_dotenv(ROOT_ENV)

# 库别名 → dataset id（与 .env 键一致；未知别名按原样当 dataset id 使用）
DATASET_ALIASES = {
    "CS_QA": os.getenv("CS_QA_DATASET_ID", ""),
    "CS_DOC": os.getenv("CS_DOC_DATASET_ID", ""),
    "SALES": os.getenv("SALES_DATASET_ID", ""),
    "DOCTOR": os.getenv("DOCTOR_DATASET_ID", ""),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Dify 知识库召回测试（读取端）")
    parser.add_argument("--query", action="append", required=True, help="测试问题（可多次）")
    parser.add_argument("--dataset", default="CS_DOC", help="库别名或 dataset id（默认 CS_DOC）")
    parser.add_argument("--method", default="hybrid_search", choices=["semantic_search", "full_text_search", "hybrid_search"])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.0, help="score 阈值（默认 0 不过滤）")
    args = parser.parse_args()

    base_url = os.getenv("DIFY_BASE_URL", "").rstrip("/")
    api_key = os.getenv("DIFY_KNOWLEDGE_API_KEY") or os.getenv("DIFY_API_KEY", "")
    if not base_url or not api_key:
        print("SKIPPED: .env 缺少 DIFY_BASE_URL / DIFY_KNOWLEDGE_API_KEY")
        return 0
    dataset_id = DATASET_ALIASES.get(args.dataset, args.dataset)
    if not dataset_id:
        print(f"SKIPPED: 未知库别名 {args.dataset}（.env 未配置）")
        return 0

    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=60.0, trust_env=False) as client:
        for query in args.query:
            print(f"\n== query: {query}  @ {args.dataset}（{args.method}, top_k={args.top_k}）")
            try:
                resp = client.post(
                    f"{base_url}/datasets/{dataset_id}/retrieve",
                    headers=headers,
                    json={
                        "query": query,
                        "retrieval_model": {
                            "search_method": args.method,
                            "reranking_enable": False,
                            "top_k": args.top_k,
                            "score_threshold_enabled": True,
                            "score_threshold": args.threshold,
                        },
                    },
                )
                resp.raise_for_status()
                records = resp.json().get("records", [])
                hits = [r for r in records if r.get("score", 0) >= args.threshold]
                print(f"召回 {len(records)} 条（≥{args.threshold} 计 {len(hits)} 条）")
                for i, r in enumerate(hits, 1):
                    seg = r.get("segment") or {}
                    doc_name = r.get("document", {}).get("name", "?")
                    score = r.get("score", 0.0)
                    content = (seg.get("content") or "").replace("\n", " ")[:80]
                    print(f"  #{i} score={score:.4f} [{doc_name}] {content}")
            except httpx.HTTPStatusError as exc:
                print(f"  [ERROR] HTTP {exc.response.status_code} {exc.response.text[:200]}")
            except httpx.HTTPError as exc:
                print(f"  [ERROR] {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

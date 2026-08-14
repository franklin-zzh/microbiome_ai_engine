"""CS 客服工作流发布后接口冒烟测试 + 基线测量（三分支）。

用法：
    1. Dify 控制台发布 cs_agent_chatbot 应用，进入「API 访问」创建 Service API 密钥
       （app- 前缀，如 DIFY_API_KEY=app-xxx 即为应用密钥），配置到 .env 的
       DIFY_CHAT_API_KEY（缺省回退 DIFY_API_KEY，与 backend dify_chat_client.py 一致）
    2. cd backend && .venv\\Scripts\\python.exe scripts\\smoke_chat.py
    3. 脚本以 blocking 模式调用 /chat-messages 跑三分支（正常回答 / 高风险转人工 /
       低置信度兜底），输出每分支状态码、全链路耗时与 token 用量，作为性能基线

未配置应用密钥时脚本安全退出（SKIPPED），不产生任何副作用。
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
# 应用密钥（app- 前缀）：与 dify_chat_client.py 约定一致，DIFY_CHAT_API_KEY 优先，回退 DIFY_API_KEY
APP_API_KEY = os.getenv("DIFY_CHAT_API_KEY") or os.getenv("DIFY_API_KEY", "")

HEADERS = {"Authorization": f"Bearer {APP_API_KEY}", "Content-Type": "application/json"}

# 三分支基线用例（与 Dify 控制台手工冒烟一致）
CASES = [
    ("正常回答", "肠道菌群检测的流程是怎样的？多久出报告？"),
    ("高风险转人工", "我最近便血，肚子还一直痛，怎么办？"),
    ("低置信度兜底", "量子纠缠对肠道菌群有什么影响？"),
]


def run_case(client: httpx.Client, name: str, query: str) -> None:
    t0 = time.monotonic()
    try:
        resp = client.post(
            "/chat-messages",
            json={"inputs": {}, "query": query, "response_mode": "blocking", "user": "smoke-test"},
        )
        elapsed = time.monotonic() - t0
        if resp.status_code != 200:
            print(f"[{name}] HTTP {resp.status_code} ({elapsed:.1f}s)\n    -> {resp.text[:200]}")
            return
        body = resp.json()
        answer = body.get("answer", "")
        usage = (body.get("metadata") or {}).get("usage") or {}
        print(f"[{name}] HTTP 200 ({elapsed:.1f}s) tokens={usage.get('total_tokens', '?')}")
        print(f"    answer: {answer[:120].replace(chr(10), ' ')}...")
    except httpx.HTTPError as exc:
        print(f"[{name}] 请求失败: {exc}")


def main() -> int:
    if not APP_API_KEY:
        print("SKIPPED: .env 缺少 DIFY_CHAT_API_KEY / DIFY_API_KEY（app- 前缀应用密钥；Dify 控制台发布应用后于「API 访问」创建或复用现有 app- 密钥）")
        return 0
    if not BASE_URL:
        print("SKIPPED: 根目录 .env 缺少 DIFY_BASE_URL")
        return 0

    print(f"== CS workflow 发布后冒烟测试 @ {BASE_URL}（{len(CASES)} 个用例，blocking 模式）")
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=180.0, trust_env=False) as client:
        for name, query in CASES:
            run_case(client, name, query)

    print("\n== 完成。耗时即当前基线（全链路 = 检索 + rerank + LLM 生成）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

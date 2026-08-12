"""一键检查后端配置能否与 Dify 跑通（只读，不修改任何 Dify 数据）。

用法：cd backend && python scripts/check_dify.py

检查项：
1. DIFY_BASE_URL 连通性 + 知识库 API 鉴权（GET /v1/datasets，用 DIFY_KNOWLEDGE_API_KEY）
2. 当前知识库列表（id / 名称 / doc_form / 是否启用 Pipeline）
3. 若配置了 CS_QA_DATASET_ID：查 datasource-plugins 并解析 local_file 节点
   （发布链路的 resolve_local_file_node_id 是否可用；Pipeline 发布目标即 CS_QA_DATASET_ID）
4. 列出 .env 缺失的关键配置项

密钥只显示前 8 位，不会完整打印。
"""
import logging
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# 只显示检查结论，不输出 httpx 请求日志
logging.getLogger("httpx").setLevel(logging.WARNING)

import httpx

from app.clients.dify_knowledge_client import DifyPipelineError, list_pipeline_datasources, resolve_local_file_node_id
from app.core.config import get_settings


def mask(key: str) -> str:
    return f"{key[:8]}...（{len(key)} 位）" if key else "（未配置）"


def main() -> int:
    settings = get_settings()
    print("=" * 60)
    print("Dify 连通性检查")
    print("=" * 60)
    print(f"DIFY_BASE_URL          : {settings.dify_base_url}")
    print(f"DIFY_KNOWLEDGE_API_KEY : {mask(settings.dify_knowledge_api_key or settings.dify_api_key)}")
    print(f"CS_QA_DATASET_ID       : {settings.cs_qa_dataset_id or '（未配置）'}  （问答库 + Pipeline 发布目标）")
    print()

    # 1. 连通 + 鉴权
    api_key = settings.dify_knowledge_api_key or settings.dify_api_key
    if not api_key:
        print("[FAIL] 未配置 DIFY_KNOWLEDGE_API_KEY / DIFY_API_KEY")
        return 1
    try:
        with httpx.Client(trust_env=False, timeout=30.0) as client:
            resp = client.get(
                f"{settings.dify_base_url.rstrip('/')}/datasets",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"page": 1, "limit": 50},
            )
            resp.raise_for_status()
            datasets = resp.json().get("data", [])
    except httpx.HTTPStatusError as exc:
        print(f"[FAIL] 鉴权/连通失败: HTTP {exc.response.status_code} {exc.response.text[:200]}")
        print("       请检查 DIFY_BASE_URL 与密钥；dataset- 前缀 key 只能调知识库 API（/datasets 等），")
        print("       不能调 /v1/info 这类 App API 端点（那需要 app- 前缀的 key，属正常权限隔离）。")
        return 1
    except httpx.HTTPError as exc:
        print(f"[FAIL] 网络不可达: {exc}")
        print("       请确认服务器 192.168.110.16:3080 可达（ping / telnet 测试）。")
        return 1

    print(f"[OK] 连通 + 知识库 API 鉴权通过，共 {len(datasets)} 个知识库：")
    for ds in datasets:
        flags = []
        if ds.get("pipeline_id"):
            flags.append("Pipeline 模式")
        if ds.get("doc_form"):
            flags.append(f"doc_form={ds['doc_form']}")
        print(f"  - {ds['id']}  {ds.get('name', '?')}  [{', '.join(flags)}]")

    # 2. Pipeline 节点解析（新发布链路的前置条件，目标库 = CS_QA_DATASET_ID）
    if settings.cs_qa_dataset_id:
        print()
        print(f"检查 Pipeline 发布链路（dataset={settings.cs_qa_dataset_id}）：")
        try:
            nodes = list_pipeline_datasources(settings.cs_qa_dataset_id)
            node_id = resolve_local_file_node_id(settings.cs_qa_dataset_id)
            print(f"[OK] datasource-plugins 返回 {len(nodes)} 个节点，local_file 节点 id = {node_id}")
        except DifyPipelineError as exc:
            print(f"[FAIL] Pipeline 节点解析失败: {exc}")
            print("       若知识库未启用 Pipeline 或没有 local_file 数据源，需在 Dify 控制台检查知识库配置。")
            return 1
    else:
        print()
        print("[WARN] CS_QA_DATASET_ID 未配置：新 CS 文档发布链路不可用（ensure_publish_target 会拒绝）。")
        print("       在 Dify 控制台「知识库」页面复制启用了 Pipeline 的知识库 id 填入 .env。")

    # 3. 缺失配置提示
    missing = []
    if not (settings.dify_knowledge_api_key or settings.dify_api_key):
        missing.append("DIFY_KNOWLEDGE_API_KEY")
    if not settings.cs_qa_dataset_id:
        missing.append("CS_QA_DATASET_ID")
    if missing:
        print()
        print(f"[WARN] .env 缺失配置：{', '.join(missing)}")
        return 1 if not settings.cs_qa_dataset_id else 0
    print()
    print("全部检查通过：后端可以调用 Dify 发布知识。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

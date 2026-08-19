"""爬虫产物批量导入后台知识库审批系统。

功能：
  1. 扫描爬虫输出目录（包含 index.md、.crawl_state.json 及各子页面 .md）；
  2. 自动解析并剥离每个 .md 的 YAML Frontmatter（url, title, depth, crawled_at）；
  3. 将纯净正文（无 YAML 干扰）落盘至 backend/public/uploads/；
  4. 写入 core_knowledge_assets 表（记录 source_type='CRAWLER', source_domain, source_url, extra_meta）；
  5. 注册 index.md 作为站点总览资产；
  6. 创建 core_knowledge_documents 和 core_knowledge_document_versions（状态为 PENDING 待审核）；
  7. 产出详细导入报告，可在管理后台一键审核并同步至 Dify。

示例：
  python -m crawler.ingest crawler/out/example_test/
  python -m crawler.ingest crawler/out/fmtbio_com/ --domain CS --category WEBSITE_KNOWLEDGE --doc-form text_model
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# 确保 backend 可被导入
ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import SessionLocal
from app.knowledge.document_services import create_document_version
from app.knowledge.models import (
    KnowledgeAsset,
    KnowledgeDocForm,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    KnowledgeDocumentVersion,
    KnowledgeDomain,
    KnowledgeVersionStatus,
)
from crawler.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)

UPLOAD_DIR = BACKEND_DIR / "public" / "uploads"


import re

def _extract_domain(url: str) -> str:
    """提取主域名（如 fmtbio.com）。"""
    netloc = urlparse(url).netloc.lower()
    return netloc.removeprefix("www.")


def _is_list_or_nav_page(rel_path: str, parsed: "ParsedMarkdown") -> tuple[bool, str]:
    """检测是否为目录页、分页列表、分类主页或低信息密度导航页。"""
    rel_lower = rel_path.lower()

    # 1. 文件名特征：子目录 index.md、index_page.md 或分页列表 index_2.html.md 等
    if re.search(r"(/|^)index(_\d+)?\.html?\.md$", rel_lower) or rel_lower.endswith("/index.md") or rel_lower.endswith("/index_page.md"):
        return True, "分类目录或分页列表页 (index/pagination)"
    if re.search(r"/(tags|category|list|search|tag)/", rel_lower):
        return True, "聚合标签/分类列表路径 (tags/category)"

    # 2. 信息密度特征检测（短行占比分析）
    lines = [line.strip() for line in parsed.clean_content.splitlines() if line.strip()]
    if not lines:
        return True, "正文为空"

    # 如果行数较多（>= 5），但短行（<= 20 字）占比超过 75%，说明全是菜单列表/电话/链接
    if len(lines) >= 5:
        short_lines = sum(1 for l in lines if len(l) <= 20)
        short_ratio = short_lines / len(lines)
        if short_ratio >= 0.75:
            return True, f"导航/目录特征明显 (短行占比 {short_ratio:.0%})"

    # 3. 面包屑导航壳（如只有几行面包屑，正文 < 350 字符）
    if ("当前位置" in parsed.clean_content or "首页 >" in parsed.clean_content) and len(parsed.clean_content) < 350:
        return True, "面包屑导航占位页 (无实质知识正文)"

    return False, ""


def _save_clean_asset_file(clean_text: str) -> tuple[str, str, int]:
    """将剥离 Frontmatter 后的纯净文本写入 uploads/ 目录。

    Returns:
        (object_key, sha256_hex, size_bytes)
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.md"
    dest = UPLOAD_DIR / filename
    content_bytes = clean_text.encode("utf-8")

    digest = hashlib.sha256(content_bytes).hexdigest()
    dest.write_bytes(content_bytes)

    return f"uploads/{filename}", digest, len(content_bytes)


def ingest_crawler_directory(
    directory: Path,
    *,
    domain_type: str = "CS",
    category: str = "WEBSITE_KNOWLEDGE",
    doc_form: str = "text_model",
    min_chars: int = 80,
    include_lists: bool = False,
    created_by: str = "crawler_importer",
    custom_tags: list[str] | None = None,
) -> dict[str, Any]:
    """将爬取目录的文档批量注册到数据库中。"""
    if not directory.exists() or not directory.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    # 读取 .crawl_state.json 或 index.md 提取站点信息
    state_file = directory / ".crawl_state.json"
    seed_url = ""
    if state_file.exists():
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            seed_url = state_data.get("seed_url", "")
        except Exception:
            pass

    # 扫描所有 .md 文件
    md_files = sorted(directory.rglob("*.md"))
    if not md_files:
        raise ValueError(f"未在目录 {directory} 中发现 .md 文件")

    # 尝试从首个文档确定 seed_url 和 domain
    site_domain = ""
    for f in md_files:
        parsed = parse_frontmatter(f.read_text(encoding="utf-8", errors="ignore"))
        if parsed.url:
            if not seed_url:
                seed_url = parsed.url
            site_domain = _extract_domain(parsed.url)
            break

    if not site_domain:
        site_domain = directory.name

    batch_id = f"crawl_{site_domain.replace('.', '_')}_{datetime.now().strftime('%Y%m%d%H%M')}"
    base_tags = [f"source:{site_domain}", "type:crawler", f"batch:{batch_id}"]
    if custom_tags:
        base_tags.extend(custom_tags)

    # 映射 doc_form 枚举
    form_enum = KnowledgeDocForm(doc_form)
    domain_enum = KnowledgeDomain(domain_type)

    db = SessionLocal()
    imported_docs = []
    skipped_docs = []
    index_asset_id = None

    try:
        # 1. 优先处理 index.md 作为总览资产（不作为独立待审文档推送）
        index_file = directory / "index.md"
        if index_file.exists():
            index_text = index_file.read_text(encoding="utf-8", errors="ignore")
            obj_key, sha, size = _save_clean_asset_file(index_text)
            index_asset = KnowledgeAsset(
                original_filename=f"[INDEX] {site_domain} index.md",
                source_type="CRAWLER",
                source_domain=site_domain,
                source_url=seed_url,
                mime_type="text/markdown",
                size_bytes=size,
                sha256=sha,
                storage_provider="LOCAL",
                object_key=obj_key,
                extra_meta={
                    "is_index": True,
                    "seed_url": seed_url,
                    "batch_id": batch_id,
                    "crawled_at": datetime.now(timezone.utc).isoformat(),
                },
                created_by=created_by,
            )
            db.add(index_asset)
            db.commit()
            db.refresh(index_asset)
            index_asset_id = index_asset.id

        # 2. 遍历处理所有子页面
        for md_path in md_files:
            if md_path.name.lower() == "index.md" and md_path.parent == directory:
                continue  # 根目录 index.md 已作为总览资产登记

            rel_path = md_path.relative_to(directory).as_posix()
            raw_text = md_path.read_text(encoding="utf-8", errors="ignore")
            parsed = parse_frontmatter(raw_text)

            # 2.1 智能检测与过滤列表/导航页
            if not include_lists:
                is_nav, nav_reason = _is_list_or_nav_page(rel_path, parsed)
                if is_nav:
                    skipped_docs.append({"path": rel_path, "reason": f"自动过滤：{nav_reason}"})
                    continue

            # 2.2 过滤字数过少的无效页面
            if len(parsed.clean_content) < min_chars:
                skipped_docs.append({"path": rel_path, "reason": f"正文过短 (<{min_chars}字符)"})
                continue

            # 2.3 计算正文哈希并写入 uploads/
            obj_key, sha, size = _save_clean_asset_file(parsed.clean_content)
            doc_title = parsed.title or md_path.stem
            page_url = parsed.url or f"https://{site_domain}/{rel_path}"

            # 2.4 检查是否已存在相同 source_url 的已有文档（实现文档更新与多版本流转）
            existing_asset = (
                db.query(KnowledgeAsset)
                .filter(
                    KnowledgeAsset.source_url == page_url,
                    KnowledgeAsset.source_domain == site_domain,
                    KnowledgeAsset.source_type == "CRAWLER",
                )
                .order_by(KnowledgeAsset.id.desc())
                .first()
            )

            existing_doc = None
            if existing_asset:
                existing_version = (
                    db.query(KnowledgeDocumentVersion)
                    .filter(KnowledgeDocumentVersion.asset_id == existing_asset.id)
                    .first()
                )
                if existing_version and existing_version.document:
                    existing_doc = existing_version.document

            # 2.5 创建资产记录
            asset = KnowledgeAsset(
                original_filename=rel_path,
                source_type="CRAWLER",
                source_domain=site_domain,
                source_url=page_url,
                mime_type="text/markdown",
                size_bytes=size,
                sha256=sha,
                storage_provider="LOCAL",
                object_key=obj_key,
                extra_meta={
                    "relative_path": rel_path,
                    "depth": parsed.depth,
                    "crawled_at": parsed.crawled_at,
                    "seed_url": seed_url,
                    "batch_id": batch_id,
                    "index_asset_id": index_asset_id,
                },
                created_by=created_by,
            )
            db.add(asset)
            db.commit()
            db.refresh(asset)

            # 2.6 文档版本流转：已有文档升级为下个版本，新文档创建 V1.0
            if existing_doc:
                if existing_asset.sha256 == sha:
                    skipped_docs.append({"path": rel_path, "reason": f"内容无变更（文档 #{existing_doc.id} 已是最新内容）"})
                    continue

                version = create_document_version(
                    db,
                    existing_doc,
                    asset,
                    title=doc_title,
                    category=category,
                    tags=base_tags,
                    pipeline_version="default",
                    created_by=created_by,
                )
                imported_docs.append({
                    "doc_id": existing_doc.id,
                    "version_id": version.id,
                    "revision": version.revision,
                    "title": doc_title,
                    "url": page_url,
                    "relative_path": rel_path,
                    "is_update": True,
                })
            else:
                doc = KnowledgeDocument(
                    domain=domain_enum,
                    title=doc_title,
                    category=category,
                    doc_form=form_enum,
                    tags=base_tags,
                    status=KnowledgeDocumentStatus.ACTIVE,
                    created_by=created_by,
                )
                db.add(doc)
                db.commit()
                db.refresh(doc)

                version = create_document_version(
                    db,
                    doc,
                    asset,
                    title=doc_title,
                    category=category,
                    tags=base_tags,
                    pipeline_version="default",
                    created_by=created_by,
                )
                imported_docs.append({
                    "doc_id": doc.id,
                    "version_id": version.id,
                    "revision": version.revision,
                    "title": doc_title,
                    "url": page_url,
                    "relative_path": rel_path,
                    "is_update": False,
                })

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "site_domain": site_domain,
        "seed_url": seed_url,
        "batch_id": batch_id,
        "index_asset_id": index_asset_id,
        "total_scanned": len(md_files),
        "imported_count": len(imported_docs),
        "skipped_count": len(skipped_docs),
        "imported": imported_docs,
        "skipped": skipped_docs,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crawler.ingest",
        description="将爬虫输出的 Markdown 目录批量导入到后台知识库审批系统",
    )
    p.add_argument("directory", help="爬虫输出目录路径（例如 crawler/out/fmtbio_com/）")
    p.add_argument("--domain", default="CS", choices=["CS", "SALES", "DOCTOR"], help="业务领域（默认 CS）")
    p.add_argument("--category", default="WEBSITE_KNOWLEDGE", help="文档业务分类（默认 WEBSITE_KNOWLEDGE）")
    p.add_argument("--doc-form", default="text_model", choices=["qa_model", "text_model", "hierarchical_model"], help="知识库形态（默认 text_model）")
    p.add_argument("--min-chars", type=int, default=80, help="最小正文字数过滤（低于此字数跳过，默认 80）")
    p.add_argument("--include-lists", action="store_true", help="是否包含分类主页与分页列表页（默认自动过滤）")
    p.add_argument("--tags", nargs="*", help="追加的自定义标签（如 tag1 tag2）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target_dir = Path(args.directory).resolve()

    print(f"[INGEST] 开始导入目录：{target_dir}")
    print(f"         业务领域：{args.domain}  分类：{args.category}  形态：{args.doc_form}")
    print()

    try:
        res = ingest_crawler_directory(
            target_dir,
            domain_type=args.domain,
            category=args.category,
            doc_form=args.doc_form,
            min_chars=args.min_chars,
            include_lists=args.include_lists,
            custom_tags=args.tags,
        )
    except Exception as exc:
        print(f"[ERROR] 导入失败: {exc}", file=sys.stderr)
        return 1

    print("=" * 60)
    print(f"[SUCCESS] 批量导入完成！")
    print(f"  站点来源：{res['site_domain']} (Seed: {res['seed_url'] or 'N/A'})")
    print(f"  批次编号：{res['batch_id']}")
    print(f"  扫描文件：{res['total_scanned']} 篇")
    print(f"  成功入库：{res['imported_count']} 篇（状态全部置为 PENDING 待审核）")
    if res['skipped_count']:
        print(f"  自动跳过：{res['skipped_count']} 篇")
    print("=" * 60)

    for item in res["imported"]:
        print(f"  + Doc #{item['doc_id']} [V{item['version_id']} PENDING] {item['title']} -> {item['url']}")

    if res["skipped"]:
        print("\n跳过文件清单：")
        for s in res["skipped"]:
            print(f"  - {s['path']} ({s['reason']})")

    print(f"\n[TIP] 现在可以在审批后台按分类或站点标签 `source:{res['site_domain']}` 批量查看并审核！")
    return 0


if __name__ == "__main__":
    sys.exit(main())

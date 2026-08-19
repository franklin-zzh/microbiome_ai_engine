"""Markdown 输出写入模块。

负责：
  1. 将单页内容写成 .md 文件（含 YAML frontmatter）
  2. 生成汇总 index.md（目录结构 + 外部链接 + 失败记录）

文件命名规则：
  URL 路径 → 本地相对路径（/ 转 OS 分隔符），根路径 → index_page.md。
  示例：
    https://example.com/          → <out>/index_page.md
    https://example.com/docs/     → <out>/docs/index.md
    https://example.com/docs/api  → <out>/docs/api.md
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from crawler.extractor import PageContent
    from crawler.session import CrawlSession

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# URL → 文件路径
# ──────────────────────────────────────────────

def url_to_relative_path(url: str) -> Path:
    """将 URL 路径部分转换为相对文件路径（.md）。

    Examples:
        https://example.com/           → index_page.md
        https://example.com/docs/      → docs/index.md
        https://example.com/docs/api   → docs/api.md
        https://example.com/docs/api/  → docs/api/index.md
    """
    parsed = urlparse(url)
    raw_path = parsed.path.rstrip("/") or "/"

    if raw_path == "/":
        return Path("index_page.md")

    parts = PurePosixPath(raw_path).parts  # ('/', 'docs', 'api')
    # 去掉根 '/'
    parts = tuple(p for p in parts if p != "/")

    if not parts:
        return Path("index_page.md")

    # 如果原 URL 以 / 结尾，末尾目录 → xxx/index.md
    if parsed.path.endswith("/"):
        return Path(*parts) / "index.md"
    else:
        stem = parts[-1]
        # 清理非法文件名字符（Windows 安全）
        stem = re.sub(r'[<>:"/\\|?*]', "_", stem)
        return Path(*parts[:-1], f"{stem}.md") if len(parts) > 1 else Path(f"{stem}.md")


# ──────────────────────────────────────────────
# 单页写入
# ──────────────────────────────────────────────

def write_page(
    content: "PageContent",
    output_dir: Path,
    depth: int,
) -> Path:
    """将单页内容写成 .md 文件。

    Args:
        content:    PageContent（url, title, text）。
        output_dir: 输出根目录。
        depth:      该页在爬取树中的深度。

    Returns:
        写入的文件绝对路径。
    """
    rel = url_to_relative_path(content.url)
    out_path = output_dir / rel
    out_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title_safe = content.title.replace('"', '\\"')

    frontmatter = (
        f"---\n"
        f'url: "{content.url}"\n'
        f'title: "{title_safe}"\n'
        f"crawled_at: {now}\n"
        f"depth: {depth}\n"
        f"---\n\n"
    )

    heading = f"# {content.title}\n\n" if content.title else ""
    body = content.text or "（页面无法提取正文）"

    out_path.write_text(frontmatter + heading + body + "\n", encoding="utf-8")
    logger.debug("写入: %s", out_path)
    return out_path


# ──────────────────────────────────────────────
# index.md 生成
# ──────────────────────────────────────────────

def _build_tree(ok_urls: list[str], output_dir: Path, seed_url: str) -> str:
    """根据 URL 路径构建 Markdown 目录树。"""
    lines: list[str] = []

    # 按路径排序
    sorted_urls = sorted(ok_urls, key=lambda u: urlparse(u).path)

    # 构建简单缩进树（按 / 深度）
    for url in sorted_urls:
        parsed = urlparse(url)
        path = parsed.path or "/"
        depth = max(0, path.rstrip("/").count("/"))
        indent = "  " * depth
        rel = url_to_relative_path(url)
        page_title = path.rstrip("/").split("/")[-1] or "(主页)"
        lines.append(f"{indent}- [{page_title}](./{rel.as_posix()}) `{url}`")

    return "\n".join(lines)


def write_index(
    session: "CrawlSession",
    output_dir: Path,
    seed_url: str,
) -> Path:
    """生成汇总 index.md。

    Args:
        session:    CrawlSession（含 visited、external、failed 信息）。
        output_dir: 输出根目录。
        seed_url:   起始 URL（用于显示）。

    Returns:
        写入的 index.md 路径。
    """
    from urllib.parse import urlparse as _up
    domain = _up(seed_url).netloc

    ok_pages = session.ok_pages()
    failed = session.failed_pages()
    external = session.external

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# 网站爬取目录：{domain}",
        "",
        f"> 爬取时间：{now}  |  入口：{seed_url}",
        f"> 共爬取页面：**{len(ok_pages)}**  |  失败：**{len(failed)}**  |  外部链接：**{len(external)}**",
        "",
        "---",
        "",
        "## 目录结构",
        "",
        _build_tree(ok_pages, output_dir, seed_url),
        "",
    ]

    # 外部链接表
    if external:
        lines += [
            "---",
            "",
            "## 外部链接（仅记录，未爬取）",
            "",
            "| 外部 URL |",
            "|---------|",
        ]
        for ext_url in external:
            lines.append(f"| {ext_url} |")
        lines.append("")

    # 失败页面表
    if failed:
        lines += [
            "---",
            "",
            "## 爬取失败的页面",
            "",
            "| URL | 原因 |",
            "|-----|------|",
        ]
        for fail_url, reason in failed:
            lines.append(f"| {fail_url} | {reason} |")
        lines.append("")

    index_path = output_dir / "index.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("已生成 index.md: %s", index_path)
    return index_path

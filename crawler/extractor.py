"""内容提取模块。

功能：
  1. extract_text()   — 从 HTML 提取标题 + 正文（使用 trafilatura，中文友好）
  2. extract_links()  — 从 HTML 提取所有 <a href> 链接，并分类为「同域」和「外域」

链接分类规则：
  - 同主域名（包括 www 变体）→ internal
  - 其他域名                → external
  - 忽略：#fragment、mailto:、tel:、javascript:、文件下载
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

try:
    import trafilatura  # type: ignore[import]
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False
    logger.warning("trafilatura 未安装，将使用 BeautifulSoup 简单提取正文")


@dataclass
class PageContent:
    """单页提取结果。"""

    url: str
    title: str = ""
    text: str = ""          # 纯文本正文（Markdown 格式）


@dataclass
class LinkSet:
    """链接分类结果。"""

    internal: list[str] = field(default_factory=list)   # 同域，待爬
    external: list[str] = field(default_factory=list)   # 跨域，仅记录


# ──────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────

def _normalize_domain(netloc: str) -> str:
    """去掉 www. 前缀，便于主域名比较。"""
    return netloc.lower().removeprefix("www.")


def _clean_url(url: str) -> str:
    """去掉 fragment，规范化尾部斜杠。"""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def _is_ignored_extension(path: str, ignored: frozenset[str]) -> bool:
    from pathlib import PurePosixPath
    suffix = PurePosixPath(path.split("?")[0]).suffix.lower()
    return suffix in ignored


# ──────────────────────────────────────────────
# 公开接口
# ──────────────────────────────────────────────

def extract_text(
    html: str,
    url: str,
    *,
    content_selector: str | None = None,
) -> PageContent:
    """从 HTML 提取标题和正文。

    Args:
        html:             页面 HTML 字符串。
        url:              页面的实际 URL（用于 trafilatura 元数据）。
        content_selector: 可选 CSS 选择器，在运行 trafilatura 前先缩小范围。

    Returns:
        PageContent 对象。
    """
    soup = BeautifulSoup(html, "lxml")

    # 提取标题
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    # 如果有 CSS 选择器，先截取目标区域
    target_html = html
    if content_selector:
        area = soup.select_one(content_selector)
        if area:
            target_html = str(area)
        else:
            logger.debug("content_selector '%s' 未匹配，使用全页", content_selector)

    # 用 trafilatura 提取正文
    text = ""
    if _HAS_TRAFILATURA:
        extracted = trafilatura.extract(
            target_html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_links=False,   # 不在正文里保留链接，保持干净
            include_images=False,
            favor_recall=True,
        )
        if extracted:
            text = extracted.strip()

    # fallback：BeautifulSoup 简单提取
    if not text:
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        body = soup.find("body") or soup
        text = body.get_text(separator="\n", strip=True)

    return PageContent(url=url, title=title, text=text)


def extract_links(
    html: str,
    base_url: str,
    root_domain: str,
    ignored_extensions: frozenset[str],
) -> LinkSet:
    """提取页面内所有链接并按域名分类。

    Args:
        html:                页面 HTML。
        base_url:            用于解析相对链接的基准 URL。
        root_domain:         爬取的主域名（如 "example.com"）。
        ignored_extensions:  需跳过的文件扩展名集合。

    Returns:
        LinkSet（internal / external 分类列表）。
    """
    soup = BeautifulSoup(html, "lxml")
    result = LinkSet()
    seen: set[str] = set()

    root_norm = _normalize_domain(root_domain)

    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()

        # 跳过无用协议
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue

        abs_url = _clean_url(urljoin(base_url, href))

        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue

        # 跳过文件下载
        if _is_ignored_extension(parsed.path, ignored_extensions):
            continue

        if abs_url in seen:
            continue
        seen.add(abs_url)

        link_domain = _normalize_domain(parsed.netloc)
        if link_domain == root_norm:
            result.internal.append(abs_url)
        else:
            result.external.append(abs_url)

    return result

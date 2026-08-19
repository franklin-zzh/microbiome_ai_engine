"""Web Crawler 配置对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# 爬取时忽略的文件扩展名（不作为 HTML 页面处理）
IGNORED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
        ".zip", ".tar", ".gz", ".rar", ".7z",
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
        ".mp4", ".mp3", ".wav", ".avi", ".mov",
        ".css", ".js", ".json", ".xml", ".rss", ".atom",
        ".woff", ".woff2", ".ttf", ".eot",
    }
)


@dataclass
class CrawlConfig:
    """爬虫全局配置。

    Attributes:
        url:              起始 URL（种子页面）。
        output:           输出根目录，所有 md 文件写入此处。
        max_depth:        最大链接跟踪深度（相对于种子页面）。
        max_pages:        最多爬取的页面总数。
        delay:            每次 HTTP 请求之间的等待秒数（礼貌性延迟）。
        force:            True 时忽略断点状态、已有 md，从头重爬。
        timeout:          单次 HTTP 请求超时秒数。
        user_agent:       请求头 User-Agent。
        content_selector: 可选 CSS 选择器，用于覆盖 trafilatura 的正文提取范围。
        ignored_extensions: 爬取时跳过的文件扩展名集合。
    """

    url: str
    output: Path
    max_depth: int = 3
    max_pages: int = 200
    delay: float = 1.0
    force: bool = False
    timeout: int = 30
    user_agent: str = (
        "Mozilla/5.0 (compatible; MicrobiomeCrawler/1.0; +https://github.com/your-org)"
    )
    content_selector: str | None = None
    ignored_extensions: frozenset[str] = field(default_factory=lambda: IGNORED_EXTENSIONS)

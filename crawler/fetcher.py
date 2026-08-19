"""页面获取模块。

两条路径：
  - 静态：requests.Session（快速、无额外依赖）
  - 动态：Playwright（无头 Chromium，等待 networkidle）

fetcher.fetch(url, config) 对外屏蔽底层差异，统一返回 (html_str, final_url)。
final_url 反映实际加载的 URL（含重定向）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests

from crawler.detector import is_dynamic

if TYPE_CHECKING:
    from crawler.config import CrawlConfig

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """HTTP 或渲染失败时抛出，携带状态码或原因描述。"""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"FETCH FAIL [{url}]: {reason}")
        self.url = url
        self.reason = reason


def _fetch_static(
    url: str,
    session: requests.Session,
    config: "CrawlConfig",
) -> tuple[str, str]:
    """用 requests 获取页面。返回 (html, final_url)。"""
    try:
        resp = session.get(url, timeout=config.timeout, allow_redirects=True)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        raise FetchError(url, f"HTTP {exc.response.status_code}") from exc
    except requests.RequestException as exc:
        raise FetchError(url, str(exc)) from exc

    # 尊重服务器声明的编码，fallback apparent_encoding
    if resp.encoding and resp.encoding.lower() not in ("iso-8859-1", "latin-1"):
        html = resp.text
    else:
        resp.encoding = resp.apparent_encoding
        html = resp.text

    return html, resp.url


def _fetch_dynamic(url: str, config: "CrawlConfig") -> tuple[str, str]:
    """用 Playwright（无头 Chromium）渲染页面。返回 (html, final_url)。"""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import]
    except ImportError as exc:
        raise FetchError(
            url,
            "Playwright 未安装：pip install playwright && playwright install chromium",
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=config.user_agent,
            java_script_enabled=True,
        )
        page = ctx.new_page()
        try:
            resp = page.goto(url, timeout=config.timeout * 1000, wait_until="networkidle")
            if resp is None or not resp.ok:
                status = resp.status if resp else "unknown"
                raise FetchError(url, f"HTTP {status}")
            html = page.content()
            final_url = page.url
        finally:
            ctx.close()
            browser.close()

    return html, final_url


def make_session(config: "CrawlConfig") -> requests.Session:
    """创建配置好 User-Agent 的 requests.Session。"""
    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})
    return session


def fetch(
    url: str,
    session: requests.Session,
    config: "CrawlConfig",
    *,
    force_static: bool = False,
) -> tuple[str, str]:
    """获取页面 HTML。

    先用静态方式请求；若检测为动态页面则自动切换 Playwright。

    Args:
        url:          目标 URL。
        session:      复用的 requests.Session。
        config:       爬虫配置。
        force_static: 跳过动态检测，强制用 requests。

    Returns:
        (html, final_url) 元组。
    """
    html, final_url = _fetch_static(url, session, config)

    if not force_static and is_dynamic(html):
        logger.info("检测到动态页面，切换 Playwright：%s", url)
        try:
            html, final_url = _fetch_dynamic(url, config)
        except FetchError:
            logger.warning("Playwright 失败，回退到静态内容：%s", url)
            # 已有静态 html，继续使用

    return html, final_url

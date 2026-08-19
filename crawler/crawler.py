"""核心爬取调度器。

执行流程：
  1. 初始化 CrawlSession（读取断点 or 新建）
  2. 将种子 URL 加入队列
  3. BFS 循环：
     a. 取队头 (url, depth)
     b. 跳过：已访问 / 超深度 / 超页数 / 已有 md（非 force 时）
     c. fetch → extract_text → write_page → extract_links → enqueue
     d. 每页后 save() 状态 + 延迟
  4. 写 index.md
  5. 打印摘要
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import urlparse

from crawler.config import CrawlConfig
from crawler.extractor import extract_links, extract_text
from crawler.fetcher import FetchError, fetch, make_session
from crawler.session import CrawlSession
from crawler.writer import url_to_relative_path, write_index, write_page

logger = logging.getLogger(__name__)


def _root_domain(url: str) -> str:
    """提取主域名（不含 www.）。"""
    netloc = urlparse(url).netloc.lower()
    return netloc.removeprefix("www.")


def crawl(config: CrawlConfig) -> CrawlSession:
    """执行完整爬取任务。

    Args:
        config: CrawlConfig 配置对象。

    Returns:
        完成后的 CrawlSession（含所有状态）。
    """
    output_dir = config.output
    output_dir.mkdir(parents=True, exist_ok=True)

    seed = config.url
    root_domain = _root_domain(seed)

    # ── 初始化 Session ─────────────────────────────
    session = CrawlSession(output_dir, seed)
    if config.force:
        session.clear(output_dir)
        print("[FORCE] 忽略断点，从头开始")
    else:
        resumed = session.load()
        if not resumed:
            # 全新任务，加入种子
            session.enqueue(seed, 0)
        # 若恢复断点，queue 里已有上次剩余的待爬项

    # 若 queue 空但 seed 未被爬，也要加一次
    if not session.queue and not session.is_visited(seed):
        session.enqueue(seed, 0)

    http_session = make_session(config)
    total_written = 0

    # ── BFS 循环 ────────────────────────────────────
    while session.queue:
        url, depth = session.dequeue()

        # ── 跳过条件 ──────────────────────────────
        if session.is_visited(url):
            continue

        if depth > config.max_depth:
            logger.debug("超过最大深度 %d，跳过 %s", config.max_depth, url)
            continue

        if session.total_visited() >= config.max_pages:
            print(f"\n[WARN] 已达最大页面数 {config.max_pages}，停止爬取")
            break

        # 断点续爬：已有 md 文件且非 force 时跳过（标记为 ok 继续计数）
        out_rel = url_to_relative_path(url)
        out_path = output_dir / out_rel
        if out_path.exists() and not config.force:
            print(f"  SKIP [{session.total_visited() + 1}] {url}  (已有 md)")
            session.mark_ok(url)
            session.save()
            continue

        # ── 获取页面 ──────────────────────────────
        page_num = session.total_visited() + 1
        print(f"  [{page_num}] 爬取 {url} (depth={depth}) ...", end=" ", flush=True)

        try:
            html, final_url = fetch(url, http_session, config)
        except FetchError as exc:
            print(f"FAIL ({exc.reason})")
            session.mark_fail(url, exc.reason)
            session.save()
            continue

        # ── 提取内容 ──────────────────────────────
        content = extract_text(html, final_url, content_selector=config.content_selector)
        if not content.title and not content.text:
            print("SKIP (无法提取内容)")
            session.mark_fail(url, "无法提取内容")
            session.save()
            continue

        # ── 写 md ─────────────────────────────────
        try:
            written = write_page(content, output_dir, depth)
            print(f"OK  → {written.relative_to(output_dir)}")
            total_written += 1
        except OSError as exc:
            print(f"FAIL (写文件失败: {exc})")
            session.mark_fail(url, f"写文件失败: {exc}")
            session.save()
            continue

        session.mark_ok(url)

        # ── 提取并分类链接 ────────────────────────
        if depth < config.max_depth:
            links = extract_links(html, final_url, root_domain, config.ignored_extensions)
            for link in links.internal:
                session.enqueue(link, depth + 1)
            session.add_external(links.external)

        # ── 持久化 & 延迟 ─────────────────────────
        session.save()
        if config.delay > 0:
            time.sleep(config.delay)

    # ── 写 index.md ─────────────────────────────────
    index_path = write_index(session, output_dir, seed)

    # ── 摘要 ────────────────────────────────────────
    ok = len(session.ok_pages())
    fail = len(session.failed_pages())
    ext = len(session.external)
    print()
    print("=" * 60)
    print(f"[DONE] 完成！爬取 {ok} 页，失败 {fail} 页，外部链接 {ext} 条")
    print(f"[INDEX] 目录：{index_path}")
    print(f"[OUT]   输出：{output_dir}")
    print("=" * 60)

    return session

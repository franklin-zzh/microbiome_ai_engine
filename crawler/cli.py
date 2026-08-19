"""Web Crawler CLI 入口。

示例：
    python -m crawler https://example.com -o ./out/example/
    python -m crawler https://docs.example.com -o ./out/docs/ --max-depth 5 --max-pages 500
    python -m crawler https://example.com -o ./out/example/ --force
    python -m crawler https://example.com -o ./out/example/ --delay 2.0 --content-selector "main article"
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from crawler.config import CrawlConfig
from crawler.crawler import crawl


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crawler",
        description="网站全站爬取 → 结构化 Markdown（支持静态/动态页面，断点续爬）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 基础爬取
  python -m crawler https://example.com -o ./out/example/

  # 限制深度和页数
  python -m crawler https://example.com -o ./out/ --max-depth 2 --max-pages 50

  # 强制重爬（忽略断点和已有文件）
  python -m crawler https://example.com -o ./out/example/ --force

  # 慢速爬取（每请求间隔 2 秒）
  python -m crawler https://example.com -o ./out/example/ --delay 2.0

  # 指定正文 CSS 选择器（覆盖自动提取）
  python -m crawler https://docs.example.com -o ./out/ --content-selector "main article"
""",
    )

    p.add_argument("url", help="起始 URL（种子页面）")
    p.add_argument("-o", "--output", required=True, help="输出目录路径")

    # 爬取控制
    p.add_argument(
        "--max-depth", type=int, default=3,
        help="最大链接跟踪深度（默认 3）",
    )
    p.add_argument(
        "--max-pages", type=int, default=200,
        help="最多爬取页面数（默认 200）",
    )
    p.add_argument(
        "--delay", type=float, default=1.0,
        help="每次请求的间隔秒数（默认 1.0，设 0 关闭）",
    )
    p.add_argument(
        "--timeout", type=int, default=30,
        help="单次请求超时秒数（默认 30）",
    )

    # 内容选项
    p.add_argument(
        "--content-selector", default=None, metavar="CSS_SELECTOR",
        help="CSS 选择器，指定提取正文的区域（如 'main article'）",
    )

    # 行为开关
    p.add_argument(
        "--force", action="store_true",
        help="强制重爬：忽略断点状态和已有 md 文件",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="输出调试日志",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # 日志配置
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s %(name)s: %(message)s",
    )

    config = CrawlConfig(
        url=args.url,
        output=Path(args.output),
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        delay=args.delay,
        timeout=args.timeout,
        content_selector=args.content_selector,
        force=args.force,
    )

    print(f"[START] 开始爬取：{config.url}")
    print(f"        输出目录：{config.output}")
    print(f"        最大深度：{config.max_depth}  最大页数：{config.max_pages}  间隔：{config.delay}s")
    if config.content_selector:
        print(f"        内容选择器：{config.content_selector}")
    print()

    try:
        crawl(config)
    except KeyboardInterrupt:
        print("\n\n[PAUSED] 用户中断。断点已保存，下次运行可继续。")
        return 130
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

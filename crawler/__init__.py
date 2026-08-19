"""Web Crawler 公开接口。

快速 API 使用：
    from crawler import crawl, CrawlConfig
    from pathlib import Path

    config = CrawlConfig(url="https://example.com", output=Path("./out/example/"))
    session = crawl(config)
    print(f"爬取完成：{len(session.ok_pages())} 页")
"""
from crawler.config import CrawlConfig
from crawler.frontmatter import parse_frontmatter


def crawl(*args, **kwargs):
    from crawler.crawler import crawl as _crawl
    return _crawl(*args, **kwargs)


def CrawlSession(*args, **kwargs):
    from crawler.session import CrawlSession as _CrawlSession
    return _CrawlSession(*args, **kwargs)


__all__ = ["CrawlConfig", "CrawlSession", "crawl", "parse_frontmatter"]

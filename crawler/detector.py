"""静态 / 动态页面自动检测。

检测逻辑：
  1. 先用 requests 做一次轻量 GET；
  2. 若响应体 <body> 内的可见文字极少（< MIN_TEXT_LEN 字符）且页面有大量 <script>
     标签，则判定为「需要 JS 渲染」的动态网站；
  3. 否则判定为静态网站。

用途：fetcher 在拿到初始 HTML 后调用本模块决定是否切换到 Playwright。
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

# 动态判断阈值
MIN_TEXT_LEN = 200       # body 可见文字低于此长度才可能是动态
MIN_SCRIPT_COUNT = 3     # 同时还要有足够多 <script> 标签


def is_dynamic(html: str) -> bool:
    """判断 HTML 是否为需要 JS 渲染的动态页面。

    Args:
        html: 服务器返回的原始 HTML 字符串。

    Returns:
        True 表示需要用 Playwright 重新渲染，False 表示静态内容足够。
    """
    soup = BeautifulSoup(html, "lxml")

    # 提取 body 可见文字（去掉 script/style 标签）
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    body = soup.find("body")
    if body is None:
        return False  # 没有 body 就当静态处理

    visible_text = re.sub(r"\s+", " ", body.get_text(separator=" ")).strip()
    script_count = len(soup.find_all("script"))  # 原始 soup 已经没 script 了，重算
    # 重新解析数 script（上面已 decompose，需原始统计）
    script_count_raw = html.lower().count("<script")

    too_little_text = len(visible_text) < MIN_TEXT_LEN
    many_scripts = script_count_raw >= MIN_SCRIPT_COUNT

    return too_little_text and many_scripts

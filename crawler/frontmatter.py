"""Markdown YAML Frontmatter 解析与剥离工具。

用于从爬虫产出的 .md 文件中提取元数据（url, title, crawled_at, depth 等），
并产出剥离了 YAML 头部的纯净正文（用于存入资产和推送到 Dify 知识库切块）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

FRONTMATTER_PATTERN = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


@dataclass
class ParsedMarkdown:
    """解析后的 Markdown 文档。"""

    metadata: dict[str, Any] = field(default_factory=dict)
    clean_content: str = ""
    raw_frontmatter: str = ""

    @property
    def title(self) -> str:
        return str(self.metadata.get("title") or "").strip()

    @property
    def url(self) -> str:
        return str(self.metadata.get("url") or "").strip()

    @property
    def depth(self) -> int:
        try:
            return int(self.metadata.get("depth", 0))
        except (ValueError, TypeError):
            return 0

    @property
    def crawled_at(self) -> str | None:
        val = self.metadata.get("crawled_at")
        return str(val) if val else None


def _simple_yaml_parse(yaml_str: str) -> dict[str, Any]:
    """无需外部依赖的轻量 YAML 键值解析器，兼容常见 frontmatter 格式。"""
    res: dict[str, Any] = {}
    for line in yaml_str.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        # 去掉包裹的引号
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        # 处理整数/布尔
        if val.lower() == "true":
            res[key] = True
        elif val.lower() == "false":
            res[key] = False
        elif val.isdigit():
            res[key] = int(val)
        else:
            res[key] = val
    return res


def parse_frontmatter(text: str) -> ParsedMarkdown:
    """从 Markdown 文本中解析并剥离 Frontmatter。

    Args:
        text: 原始 Markdown 内容字符串。

    Returns:
        ParsedMarkdown 对象，包含元数据字典和纯净正文。
    """
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        return ParsedMarkdown(metadata={}, clean_content=text.strip(), raw_frontmatter="")

    raw_fm = match.group(1)
    clean_body = text[match.end():].strip()

    metadata = _simple_yaml_parse(raw_fm)
    return ParsedMarkdown(
        metadata=metadata,
        clean_content=clean_body,
        raw_frontmatter=raw_fm,
    )

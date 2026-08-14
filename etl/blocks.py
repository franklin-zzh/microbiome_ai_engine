"""中间表示：清洗管道各 converter 产出的统一 Block 结构，由 mdwriter 渲染为 Markdown。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Block:
    kind: str  # heading | paragraph | table | image | markdown(原始 md 透传) | quote
    level: int = 0  # heading 级别 1-6
    text: str = ""  # 段落/标题/引用/透传 md 文本
    rows: list[list[str]] | None = None  # 表格数据（kind=table）
    image_path: Path | None = None  # 图片文件路径（kind=image）
    caption: str = ""  # 图片说明/来源位置
    meta: dict = field(default_factory=dict)  # 附加信息（页号等）

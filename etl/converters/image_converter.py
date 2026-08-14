"""图片文件 → Block：整图作为 image 块，由 mdwriter 渲染时执行 OCR 并把识别文本输出为可见引用块。"""
from __future__ import annotations

from pathlib import Path

from etl.blocks import Block


def extract(path: Path, workdir: Path) -> list[Block]:
    return [Block(kind="image", image_path=path, caption=path.stem)]

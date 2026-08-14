"""Markdown 渲染：Block 列表 → 统一 md 文本。

约定：
- 文件头写入源文件元数据注释；
- 图片块：复制到输出 assets 目录，md 中输出「图片引用 + 图片文字识别（可见引用块）」双通道；
- OCR 由 etl.ocr.extract_text 提供（rapidocr 懒加载），未安装时静默降级为仅图片引用。
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from etl.blocks import Block

# OCR 模块懒加载：缺依赖时降级（图片仅保留引用），不阻塞管道
try:
    from etl.ocr import extract_text as _ocr_extract  # noqa: F401
except ImportError:  # pragma: no cover - 依赖缺失时降级
    _ocr_extract = None


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _render_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    normalized = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(_escape_cell(c) for c in normalized[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for row in normalized[1:]:
        lines.append("| " + " | ".join(_escape_cell(c) for c in row) + " |")
    return "\n".join(lines)


def render_blocks(
    blocks: list[Block],
    *,
    assets_dir: str = "assets",
    assets_abs: Path | None = None,
    ocr_enabled: bool = True,
    meta: dict | None = None,
) -> str:
    """渲染 blocks 为 md 文本。assets_abs 为输出资产目录的绝对路径（缺省时不复制图片）。"""
    lines: list[str] = []
    if meta:
        lines.append(
            f"<!-- 来源: {meta.get('source', '?')} | 格式: {meta.get('format', '?')} | "
            f"清洗: {datetime.now().strftime('%Y-%m-%d %H:%M')} -->\n"
        )

    image_seq = 0
    for block in blocks:
        if block.kind == "heading":
            level = max(1, min(6, block.level))
            lines.append(f"{'#' * level} {block.text}\n")
        elif block.kind == "paragraph":
            lines.append(f"{block.text}\n")
        elif block.kind == "quote":
            lines.append(f"> {block.text}\n")
        elif block.kind == "markdown":
            lines.append(f"{block.text.strip()}\n")
        elif block.kind == "table":
            lines.append(_render_table(block.rows or []) + "\n")
        elif block.kind == "image":
            image_seq += 1
            rel_ref = None
            ocr_text = ""
            if block.image_path and block.image_path.is_file():
                # 复制到 assets 目录（重命名防冲突），md 引用相对路径
                if assets_abs is not None:
                    assets_abs.mkdir(parents=True, exist_ok=True)
                    ext = block.image_path.suffix.lower() or ".png"
                    target = assets_abs / f"img-{image_seq:03d}{ext}"
                    try:
                        shutil.copyfile(block.image_path, target)
                        rel_ref = f"{assets_dir}/{target.name}"
                    except OSError:
                        rel_ref = None
                if ocr_enabled and _ocr_extract is not None:
                    try:
                        ocr_text = (_ocr_extract(block.image_path) or "").strip()
                    except Exception:  # OCR 失败不阻断管道
                        ocr_text = ""
            caption = block.caption or f"图片{image_seq}"
            if rel_ref:
                lines.append(f"![{caption}]({rel_ref})\n")
            else:
                lines.append(f"<!-- 图片[{image_seq}]: {block.image_path or caption}（未找到文件） -->\n")
            if ocr_text:
                # OCR 文本作为可见引用块输出（HTML 注释可能不被 Dify 切分保留，可见文本才可靠）
                lines.append(f"> 【图片文字】{ocr_text}\n")
        # 未知 kind 忽略

    return "\n".join(lines)

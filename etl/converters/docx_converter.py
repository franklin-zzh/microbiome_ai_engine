"""DOCX → Block：markitdown（微软官方）优先，docx2python 保底。

- markitdown：段落/标题/表格/图片引用一站式转 md，直接透传（kind=markdown），
  媒体文件经 dest 参数保存到 workdir（旧版无 dest 时仅保留引用占位）。
- docx2python（保底）：正文段落/表格结构化提取，separate_images 抽图。
"""
from __future__ import annotations

from pathlib import Path

from etl.blocks import Block


def _extract_docx2python(path: Path, workdir: Path) -> list[Block]:
    from docx2python import docx2python

    blocks: list[Block] = []
    with docx2python(str(path), separate_images=True) as docx_content:
        image_dir = Path(docx_content.image_dir) if docx_content.image_dir else None
        body = docx_content.body  # list[list[list[str]]]，深层为段落/单元格文本
        for section in body:
            for paragraph_or_table in section:
                # 段落：list[str]；表格：list[list[str]]（含表头判断）
                if isinstance(paragraph_or_table, list) and paragraph_or_table and isinstance(
                    paragraph_or_table[0], str
                ):
                    text = " ".join(paragraph_or_table).strip()
                    if text:
                        blocks.append(Block(kind="paragraph", text=text))
                elif isinstance(paragraph_or_table, list) and paragraph_or_table and isinstance(
                    paragraph_or_table[0], list
                ):
                    rows = [[cell.strip() for cell in row] for row in paragraph_or_table]
                    blocks.append(Block(kind="table", rows=rows))
        if image_dir and image_dir.is_dir():
            for n, img in enumerate(sorted(image_dir.iterdir()), 1):
                blocks.append(Block(kind="image", image_path=img, caption=f"docx 图片{n}"))
    return blocks


def extract(path: Path, workdir: Path) -> list[Block]:
    try:
        from markitdown import MarkItDown
    except ImportError:
        return _extract_docx2python(path, workdir)

    converter = MarkItDown()
    try:
        result = converter.convert(str(path), dest=workdir)  # dest 保存媒体文件（新版）
    except TypeError:
        result = converter.convert(str(path))  # 旧版无 dest 参数：仅引用占位
    except Exception:  # MissingDependencyException 等（缺 [docx] extra）→ 保底 docx2python
        return _extract_docx2python(path, workdir)
    text = (result.text_content or "").strip()
    if not text:
        return _extract_docx2python(path, workdir)
    # 透传 markitdown 的 md 文本；图片引用指向 dest 保存的文件（若存在）
    blocks: list[Block] = [Block(kind="markdown", text=text)]
    # 将 dest 目录下保存的媒体文件补为 image 块（引用地址以相对路径写出）
    for n, img in enumerate(sorted(workdir.glob("*")), 1):
        if img.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}:
            blocks.append(Block(kind="image", image_path=img, caption=f"docx 媒体{n}"))
    return blocks

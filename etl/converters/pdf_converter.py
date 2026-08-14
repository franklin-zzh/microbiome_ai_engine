"""PDF → Block：
- 原生 PDF（有文本层）：pymupdf4llm 直接转 md（速度极快、质量高），媒体文件经 write_images 保存；
- 扫描件 PDF（无文本层）：PyMuPDF 按页渲染 PNG → image 块，整页交 OCR（无需 Poppler）。
"""
from __future__ import annotations

from pathlib import Path

try:
    import pymupdf  # pymupdf>=1.28（fitz 旧别名已废弃）
except ImportError:  # pragma: no cover - 兼容老版本
    import fitz as pymupdf

from etl.blocks import Block


def _has_text_layer(path: Path) -> bool:
    try:
        with pymupdf.open(str(path)) as doc:
            return any(page.get_text().strip() for page in doc)
    except Exception:
        return False


def extract(path: Path, workdir: Path) -> list[Block]:
    if not _has_text_layer(path):
        return _extract_scanned(path, workdir)

    try:
        import pymupdf4llm

        md_text = pymupdf4llm.to_markdown(str(path), write_images=True, image_path=str(workdir))
    except TypeError:  # 旧版无 write_images 参数
        import pymupdf4llm

        md_text = pymupdf4llm.to_markdown(str(path))
    except Exception:
        # 极端兜底：pymupdf4llm 失败时退化为逐页纯文本
        return _extract_text_fallback(path)

    blocks: list[Block] = []
    if md_text and md_text.strip():
        blocks.append(Block(kind="markdown", text=md_text))
    # pymupdf4llm 保存的页面图片补为 image 块（OCR 识别图内文字）
    for n, img in enumerate(sorted(workdir.glob("*")), 1):
        if img.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}:
            blocks.append(Block(kind="image", image_path=img, caption=f"pdf 媒体{n}"))
    return blocks


def _extract_scanned(path: Path, workdir: Path) -> list[Block]:
    """扫描件：每页渲染为 200dpi PNG，整页交给 OCR（mdwriter 渲染时执行）。"""
    workdir.mkdir(parents=True, exist_ok=True)
    blocks: list[Block] = []
    with pymupdf.open(str(path)) as doc:
        if doc.page_count == 0:
            return blocks
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=200)
            img_path = workdir / f"page{i:03d}.png"
            pix.save(str(img_path))
            blocks.append(Block(kind="image", image_path=img_path, caption=f"第{i}页（扫描件）"))
    return blocks


def _extract_text_fallback(path: Path) -> list[Block]:
    """pymupdf4llm 不可用时：逐页文本 + 渲染图片兜底。"""
    blocks: list[Block] = []
    with pymupdf.open(str(path)) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text().strip()
            blocks.append(Block(kind="heading", level=2, text=f"第 {i} 页"))
            if text:
                blocks.append(Block(kind="markdown", text=text))
    return blocks

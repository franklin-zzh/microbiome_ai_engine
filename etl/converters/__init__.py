"""converter 注册表：扩展名 → 提取函数（按格式分派）。"""
from __future__ import annotations

SUPPORTED_EXTS = {".docx", ".pptx", ".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def get_converter(ext: str):
    """按扩展名返回 converter 的 extract(path, workdir) -> list[Block] 函数。"""
    ext = ext.lower()
    if ext == ".docx":
        from etl.converters.docx_converter import extract
    elif ext == ".pptx":
        from etl.converters.pptx_converter import extract
    elif ext == ".pdf":
        from etl.converters.pdf_converter import extract
    elif ext in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        from etl.converters.image_converter import extract
    else:
        raise ValueError(f"不支持的文件格式: {ext}")
    return extract

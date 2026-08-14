"""分派器：输入文件/目录 → 按扩展名路由 converter → Block → mdwriter 渲染 → 写 .md。

流程：convert_path(src, dst) → convert_file(每文件)：
  1. converter.extract(src, tmp_workdir) 产出 Block 列表（图片先抠到临时目录）；
  2. mdwriter.render_blocks 把图片复制进输出 assets 目录并执行 OCR（启用时）；
  3. 写 .md，清理临时目录。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from etl.blocks import Block
from etl.converters import SUPPORTED_EXTS, get_converter
from etl.mdwriter import render_blocks


@dataclass
class ConvertResult:
    source: str
    output: str
    blocks: int = 0
    images: int = 0
    ocr_done: int = 0
    skipped: bool = False


def _iter_sources(src: Path) -> list[Path]:
    if src.is_file():
        return [src] if src.suffix.lower() in SUPPORTED_EXTS else []
    return sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS)


def convert_file(
    src: Path,
    out_md: Path,
    *,
    ocr: bool = True,
    assets_dir: str = "assets",
    force: bool = False,
) -> ConvertResult:
    result = ConvertResult(str(src), str(out_md))
    if out_md.exists() and not force:
        result.skipped = True
        return result

    workdir = out_md.parent / f".{out_md.stem}_etl_tmp"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        extract = get_converter(src.suffix.lower())
        blocks: list[Block] = extract(src, workdir)
        meta = {"source": src.name, "format": src.suffix.lower().lstrip(".")}
        assets_abs = out_md.parent / assets_dir
        md_text = render_blocks(
            blocks,
            assets_dir=assets_dir,
            assets_abs=assets_abs,
            ocr_enabled=ocr,
            meta=meta,
        )
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(md_text, encoding="utf-8")
        result.blocks = len(blocks)
        result.images = sum(1 for b in blocks if b.kind == "image")
        result.ocr_done = md_text.count("【图片文字】")
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def convert_path(
    src: Path,
    dst: Path,
    *,
    ocr: bool = True,
    assets_dir: str = "assets",
    force: bool = False,
) -> list[ConvertResult]:
    """入口：src 为文件或目录；dst 为 .md 路径（单文件）或输出目录（目录/批量）。"""
    sources = _iter_sources(src)
    results: list[ConvertResult] = []
    for s in sources:
        if src.is_file():
            out_md = dst if dst.suffix.lower() == ".md" else dst / f"{s.stem}.md"
            per_file_assets = assets_dir
        else:
            out_md = dst / f"{s.stem}.md"
            per_file_assets = f"{s.stem}_{assets_dir}"  # 批量时每文件独立资产目录防冲突
        results.append(convert_file(s, out_md, ocr=ocr, assets_dir=per_file_assets, force=force))
    return results

"""ETL CLI 入口。

示例：
    python -m etl 产品手册.pptx -o out/产品手册.md
    python -m etl ./raw/ -o ./out/                 # 目录批量：每个文件一个 md + assets/
    python -m etl 扫描件.pdf -o out/扫描件.md --no-ocr   # 跳过图片 OCR（仅图片引用占位）
"""
import argparse
import sys
from pathlib import Path

from etl.pipeline import convert_path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="etl",
        description="多格式文档清洗为标准化 Markdown（本地 ETL，Python 3.14 兼容）",
    )
    p.add_argument("input", help="输入文件或目录")
    p.add_argument("-o", "--output", required=True, help="输出 .md 路径（单文件输入）或输出目录（目录/批量输入）")
    p.add_argument("--no-ocr", action="store_true", help="跳过图片文字 OCR（仅保留图片引用占位）")
    p.add_argument("--assets-dir", default="assets", help="图片资源子目录名（默认 assets）")
    p.add_argument("--force", action="store_true", help="输出已存在时覆盖（默认跳过）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    src = Path(args.input)
    dst = Path(args.output)
    try:
        results = convert_path(
            src,
            dst,
            ocr=not args.no_ocr,
            assets_dir=args.assets_dir,
            force=args.force,
        )
    except Exception as exc:  # 顶层兜底，保证 CLI 报错友好
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if not results:
        print("未发现受支持的文件（.docx/.pptx/.pdf/.jpg/.jpeg/.png/.bmp/.webp）", file=sys.stderr)
        return 1
    for item in results:
        print(f"OK  {item.source} -> {item.output}  ({item.blocks} blocks, {item.images} images, ocr={item.ocr_done})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""生成 ETL 冒烟样例（需已安装 etl/requirements.txt）：
- out/sample.pptx：3 页——①标题+正文+表格 ②嵌入「带中文文字」的图片 ③备注页
- out/chart_text.png：纯图片 OCR 样例（解决「图片里文字没提取」痛点的最小复现）
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/Deng.ttf",
)


def make_text_image(path: Path, text: str) -> None:
    font_path = next((p for p in FONT_CANDIDATES if Path(p).exists()), None)
    font = ImageFont.truetype(font_path, 44) if font_path else ImageFont.load_default()
    img = Image.new("RGB", (1280, 320), "white")
    draw = ImageDraw.Draw(img)
    draw.text((40, 90), text, fill="black", font=font)
    img.save(path)


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    from pptx import Presentation
    from pptx.util import Inches

    img_path = out_dir / "chart_text.png"
    make_text_image(img_path, "菌群丰度：拟杆菌门 42%，厚壁菌门 38%，放线菌门 12%")

    prs = Presentation()
    # 第 1 页：标题 + 正文 + 表格
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "肠道菌群检测产品介绍"
    slide.placeholders[1].text = "本产品通过粪便样本分析肠道菌群组成，采样后 7 个工作日出具报告。"
    table_shape = slide.shapes.add_table(3, 2, Inches(1), Inches(3), Inches(6), Inches(1.5))
    table = table_shape.table
    for r, row in enumerate([["指标", "说明"], ["采样", "粪便采样盒"], ["报告", "7 个工作日"]]):
        for c, value in enumerate(row):
            table.cell(r, c).text = value

    # 第 2 页：嵌入带文字图片（用户痛点场景）
    slide2 = prs.slides.add_slide(prs.slide_layouts[5])
    slide2.shapes.add_picture(str(img_path), Inches(1), Inches(1), width=Inches(7))
    slide2.notes_slide.notes_text_frame.text = "讲解：菌群丰度占比是报告核心指标。"

    prs.save(str(out_dir / "sample.pptx"))
    print(f"saved {out_dir / 'sample.pptx'}")
    print(f"saved {img_path}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("out"))

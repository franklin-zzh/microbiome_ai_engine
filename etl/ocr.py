"""图片文字提取：rapidocr_onnxruntime（PP-OCR 模型 + onnxruntime 推理，Python 3.14 兼容）。

设计：
- 懒加载：首次调用才 import/实例化引擎；未安装依赖时抛 ImportError，由 mdwriter 降级（仅图片引用）；
- 单例引擎缓存，避免重复加载模型（首次加载会下载/加载模型文件，约 10-30s）；
- extract_text(path) -> str：按行拼接识别文本（每行一段）；
- describe_image：VLM 兜底位（预留），用于复杂图表/表格的结构化描述，当前返回空串未启用。
"""
from __future__ import annotations

from pathlib import Path

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR  # 未安装时 ImportError，由调用方降级

        _engine = RapidOCR()
    return _engine


def extract_text(image_path: Path, *, min_confidence: float = 0.5) -> str:
    """识别图片内文字，按阅读顺序拼接（每行一段）。返回空串表示未识别到文字。"""
    engine = _get_engine()
    result, _ = engine(str(image_path))  # result: list[[box, text, confidence], ...]
    if not result:
        return ""
    lines: list[str] = []
    for item in result:
        if len(item) < 3:
            continue
        text = str(item[1]).strip()
        try:
            conf = float(item[2])
        except (TypeError, ValueError):
            conf = 1.0
        if text and conf >= min_confidence:
            lines.append(text)
    return "\n".join(lines)


def describe_image(image_path: Path) -> str:
    """VLM 兜底位（预留）：复杂图表/表格的结构化描述。当前未启用，返回空串。"""
    return ""

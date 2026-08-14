"""本地 ETL：多格式文档 -> 标准化 Markdown（在 Dify 之外完成清洗，Dify 只做干净 md 的切割/入库）。

用法：python -m etl <input> -o <output>
支持格式：.docx / .pptx / .pdf（原生+扫描件）/ .jpg .png 等图片
"""
__version__ = "0.1.0"

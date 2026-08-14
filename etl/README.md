# 本地 ETL：多格式文档 → 标准化 Markdown

> 在 Dify 之外完成文档清洗（`.docx` / `.pptx` / `.pdf` / 图片），统一产出标准化 Markdown；
> 清洗后的干净 md 再走现有 pipeline 入库 Dify（qa_model → cs_qa，text_model → cs_general）。
> 动机：Dify 内置解析是黑盒（如 PPT→PDF 中转只捞图片、不识别图片内文字），本地清洗可控、可复核、可重跑。

## 快速开始

```powershell
# 独立虚拟环境（与 backend 解耦，重依赖不污染服务镜像）
cd etl
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 单文件
python -m etl ..\raw\产品手册.pptx -o out\产品手册.md

# 目录批量（每个文件一个 md，图片进 out\assets\）
python -m etl ..\raw\ -o out\
```

## 格式分派矩阵（按文件扩展名自动路由）

| 格式 | 解析器 | 处理策略 |
|---|---|---|
| `.docx` | markitdown（保底 docx2python） | 段落/标题/表格 → md |
| `.pptx` | python-pptx | 原生文本+表格逐页提取；**图片抠出 → OCR**（rapidocr，PP-OCR 模型） |
| `.pdf`（原生，有文本层） | pymupdf4llm | 页面 → md（速度极快、质量高） |
| `.pdf`（扫描件，无文本层） | PyMuPDF 渲染页面为 PNG | 整页送 OCR（无需 Poppler） |
| `.jpg/.png/.bmp/.webp` | 图片 | 整图送 OCR → 文本块 |

## 图片文字提取

- OCR 引擎：`rapidocr_onnxruntime`（PP-OCRv4/v5 模型 + onnxruntime 推理）。
- 为什么不用 paddlepaddle：Python 3.14 无 cp314 wheel（`pip install paddlepaddle` 报 No matching distribution）。
  模型同源（PP-OCR），效果一致；后续如有 Python ≤3.12 环境可无痛换回 paddleocr（`etl/ocr.py` 已抽象后端）。
- 复杂图表/表格若 OCR 效果不理想，预留 VLM 兜底位（`etl/ocr.py` 的 `describe_image` 扩展点）。

## 输出约定

- 单文件 → 同名 `.md`；目录批量 → 每个源文件一个 md。
- 图片统一存入输出目录下 `assets/`，md 中保留「图片引用 + 图片文字识别」双通道：
  ```markdown
  ![图表-1](assets/图表-1.png)

  > 【图片文字】菌群丰度占比：拟杆菌门 42%，厚壁菌门 38% ...
  ```
- 首部写入源文件元数据（来源文件/格式/清洗时间）。

## 已知问题与避坑

（详见 `docs/ETL-NOTES.md`，随实测持续回填）

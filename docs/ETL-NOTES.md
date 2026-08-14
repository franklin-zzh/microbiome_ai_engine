# ETL 清洗实测记录（2026-08-14 首轮冒烟）

> 对应 6 周计划 W2 Task 2.1（原始数据 → 标准 Markdown）。本文档随真实业务文件实测持续回填。

## 一、各格式清洗效果（冒烟实测结论）

| 格式 | 解析器 | 实测效果 | 备注 |
|---|---|---|---|
| `.pptx` | python-pptx | 标题/正文/表格/图片/演讲者备注全部提取 ✅；**图片内文字 OCR 识别 ✅** | 不走 PDF 中转，保阅读顺序与版式层级 |
| `.pdf` 原生（有文本层） | pymupdf4llm | 中文文本提取完整、自动识别标题层级 ✅ | 速度极快、质量高（当前原生 PDF 最佳解） |
| `.pdf` 扫描件（无文本层） | PyMuPDF 渲染 200dpi + rapidocr | 整页 OCR 识别 ✅ | 无需安装 Poppler（渲染由 PyMuPDF 完成） |
| `.docx` | markitdown | 标题/段落/表格 ✅ | 需 `markitdown[docx]` extra；docx2python 保底 |
| `.jpg/.png` 等图片 | rapidocr | 整图 OCR 识别 ✅ | 输出图片引用 + 文字识别可见块 |

样例产物：`etl/out/`（已 gitignore），样例生成脚本 `etl/scripts/make_samples.py` / `make_more_samples.py`。

## 二、图片文字提取（用户痛点复现与解决）

- **痛点**：PPT→PDF 中转后，Dify 解析只把图片当二进制捞出来，图片内文字零提取（缺 OCR 环节）。
- **方案**：PPT 图片直接抠出 → rapidocr（PP-OCR 模型 + onnxruntime）→ md 中输出可见引用块：
  ```markdown
  ![第2页图片1](assets/img-001.png)

  > 【图片文字】菌群丰度：拟杆菌门42%，厚壁菌门38%，放线菌门12%
  ```
- **实测**：样例图三行菌群数据 100% 识别。选择「可见引用块」而非 HTML 注释，避免 Dify 切分时丢弃。

## 三、避坑清单（实测踩坑记录）

1. **paddlepaddle 无 cp314 wheel**（`pip install paddlepaddle` 报 No matching distribution，Python 3.14 无包）→ 改用 `rapidocr_onnxruntime`：同款 PP-OCR 模型 + onnxruntime 推理，效果一致且免编译；将来有 Python ≤3.12 环境可换回 paddleocr（`etl/ocr.py` 已抽象）。
2. **markitdown 的 docx 支持需要 `[docx]` extra**：默认安装下 docx 转换抛 `MissingDependencyException`；requirements 用 `markitdown[docx]>=0.1.1`，且 docx_converter 捕获转换异常回退 docx2python。
3. **PyMuPDF 1.28+ 的 `import fitz` 已废弃**（打印 deprecation warning）→ 统一 `import pymupdf`（兼容回退 `import fitz as pymupdf`）。
4. **fitz `insert_text` 中文乱码**：默认字体（helv）不含中文字形，生成样例 PDF 时需 `fontfile` 指定中文字体（如 simhei.ttf）+ `fontname`；真实 PDF 无此问题（本坑只在样例生成脚本）。
5. **`python -m etl` 必须在项目根目录运行**（etl 是顶层包）；目录批量时每文件产出独立 assets 目录（`{stem}_assets`）防图片重名冲突。
6. **扫描件判定规则**：任一页 `get_text()` 非空即视为原生 PDF；整页无文本则按页渲染走 OCR。⚠️ **混合 PDF**（部分页是扫描图）目前会走原生路径、图片页内容可能丢失——后续优化点（按页判定 + 页级分派）。
7. **OCR 细节**：数字与中文间空格常被吞（`42 %` → `42%`）；竖排文字、密集图表识别弱；复杂图表/表格建议 VLM 兜底（`etl/ocr.py` 的 `describe_image` 已预留扩展位，未启用）。

## 四、入库 Dify 的图片处理策略（2026-08-14 落地）

1. **图片二进制不进 Dify**：Dify 1.16 RAG 是文本检索（未启用多模态），图片文件上传后不会被检索/切分，徒增存储。入库的 md 只含文本。
2. **md 中保留双通道**：
   - 图片引用行（`![第2页图片1](assets/img-001.png)`）——作为上下文线索，说明此处原有一张图；
   - OCR/文字识别块（`> 【图片文字】…`）——信息主体，Dify 切分时作为正常文本参与检索。
3. **图片文件本体留本地**：ETL 输出 `{stem}_assets/` 目录归档，供人工查阅与复核；后续若启用多模态检索，可另行上传或由 `etl/ocr.py` 的 `describe_image`（VLM 兜底位）生成图表描述写回 md 再入库。

## 五、入库 SOP（ETL md → Dify，复用已有 pipeline）

```powershell
# 1. ETL 清洗（项目根目录）
etl\.venv\Scripts\python.exe -m etl ..\raw\ -o etl\out\

# 2. 批量入库（backend 运行中；text_model → cs_general / cs_qa 用 --doc-form qa_model）
cd backend && .venv\Scripts\python.exe scripts\import_etl_md.py --md-dir ..\etl\out\ --category GENERAL

# 3. 更新已入库文档 / 只上传人工审核 / 撤下文档（--revoke 模式见脚本 --help）
```

## 六、待办

- [ ] 真实业务文件（用户 PPT/PDF/公众号文章/说明书）全量清洗验证与效果记录（Phase 4）
- [ ] 混合 PDF 按页分派处理（原生页走 pymupdf4llm，扫描页走 OCR）
- [ ] 复杂图表 VLM 兜底接入（可选，需确认多模态模型与成本）
- [ ] Semantic Chunking / FAQ Q-to-Q 工具（Phase 4 Task 2.2）

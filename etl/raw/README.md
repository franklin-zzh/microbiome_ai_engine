# 原始资料目录（raw）

把需要清洗入库的公司资料放在这里（支持子目录），然后执行：

```powershell
# 项目根目录执行：全量清洗（所有支持的格式 → 标准 md，含图片 OCR）
etl\.venv\Scripts\python.exe -m etl etl\raw -o etl\out

# 检查清洗产物（etl\out\*.md + 各 {stem}_assets\ 图片归档）
# 抽样人工核对后批量入库（text_model → cs_general；FAQ 问答对用 --doc-form qa_model → cs_qa）
cd backend; .venv\Scripts\python.exe scripts\import_etl_md.py --md-dir ..\etl\out --category GENERAL

# 更新已入库文档（同名 title 上传新版本并审核）
cd backend; .venv\Scripts\python.exe scripts\import_etl_md.py --md-dir ..\etl\out --update

# 撤下文档（删除 Dify 投影）
cd backend; .venv\Scripts\python.exe scripts\import_etl_md.py --md-dir ..\etl\out --revoke
```

支持格式：`.docx` / `.pptx` / `.pdf`（原生+扫描件）/ `.jpg .jpeg .png .bmp .webp`
（`.ppt` 旧格式与 `.xlsx` 暂不支持，先转存为上述格式再放入）。

FAQ 问答对（Q-to-Q）：CSV/XLSX/MD → qa_model 格式，见 `etl/scripts/faq_to_md.py --help`。

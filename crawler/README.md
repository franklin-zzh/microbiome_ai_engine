# 网页全站爬虫与知识库入库系统（Web Crawler & Knowledge Ingest）

> 专门用于自动化抓取目标网站及其同域下的所有子链接，智能过滤噪音（导航栏、页脚、列表页等），生成标准化的 Markdown 文档树，并提供一键批量导入到后台知识库审批系统（无缝对接 MySQL `core_knowledge_*` 表与 Dify RAG Pipeline）。

---

## 目录

- [一、核心特性](#一核心特性)
- [二、两步极简工作流](#二两步极简工作流)
- [三、命令参数详解](#三命令参数详解)
  - [1. 爬取命令 (crawl.bat)](#1-爬取命令-crawlbat)
  - [2. 批量入库命令 (ingest.bat)](#2-批量入库命令-ingestbat)
- [四、端到端数据流与架构设计](#四端到端数据流与架构设计)
- [五、数据库模型设计 (Alembic Migration)](#五数据库模型设计-alembic-migration)
- [六、关键算法与技术细节](#六关键算法与技术细节)
  - [1. 静态/动态网页自适应检测](#1-静态动态网页自适应检测)
  - [2. YAML Frontmatter 解析与剥离](#2-yaml-frontmatter-解析与剥离)
  - [3. 分类列表与分页导航智能过滤 (Anti-Nav Filter)](#3-分类列表与分页导航智能过滤-anti-nav-filter)
  - [4. URL 溯源与文章多版本流转](#4-url-溯源与文章多版本流转)
  - [5. Dify 文件名清洗 (Sanitize Filename)](#5-dify-文件名清洗-sanitize-filename)
- [七、审核后台（Admin UI）功能与接口](#七审核后台admin-ui功能与接口)
- [八、项目文件结构](#八项目文件结构)

---

## 一、核心特性

- 🌐 **同域爬取与外链记录**：基于 BFS（广度优先）调度，严格限制在主域名内深度爬取；遇到的跨域外链自动归档在 `index.md`。
- 🔄 **静态 / 动态页面自适应**：
  - 静态页面：优先使用轻量快速的 `requests` + `BeautifulSoup`。
  - 单页应用（SPA / React / Vue）：自动探测并无缝回退至 `Playwright`（无头 Chromium）渲染。
- 📝 **智能正文提取与 Frontmatter 剥离**：
  - 爬取阶段：采用 `trafilatura` 提取正文，附带 YAML Frontmatter（包含原始 URL、抓取时间、深度）。
  - 入库阶段：解析 Frontmatter 存入数据库元数据，**彻底剥离 YAML 头部生成纯净正文**，防止干扰 Dify 向量分块。
- 🛡️ **智能列表与分页过滤 (Anti-Nav)**：
  - 自动识别并跳过 `*/index.md`、`*/index_\d+.html.md` 以及短行占比 $\ge 75\%$ 的导航/目录壳页面，仅保留高价值正文。
- 🗂️ **全站目录 `index.md` 关联**：
  - `index.md` 注册为站点总览资产留存数据库，在后台提供全站地图；默认不推送到 Dify 知识库，避免稀释问答 Top-K 检索。
- ⏸️ **断点续爬与容错**：自动维护 `.crawl_state.json`，遇到网络中断或主动暂停后，再次启动自动续爬未完成页面。
- 📥 **一键批量入库与管理审批**：
  - 自动创建 `core_knowledge_documents` 与 `core_knowledge_document_versions`（初始状态为 `PENDING` 待审核）。
  - 后台支持**行内就地下拉预览正文**与**一键批量通过并发布到 Dify**。

---

## 二、两步极简工作流

从爬取到导入后台审核，仅需两条根目录命令：

### 第一步：爬取整站文档
```powershell
.\crawl.bat https://fmtbio.com -o ./crawler/out/fmtbio/
```

### 第二步：一键批量导入后台待审核列表
```powershell
.\ingest.bat ./crawler/out/fmtbio/
```

> **执行效果**：
> 1. 自动过滤 70+ 个列表与分页壳页面，仅将包含实质内容的文章详情页录入；
> 2. 剥离 YAML 头部并落盘纯净正文至 `backend/public/uploads/`；
> 3. 打开后台（`http://localhost:8000/public/admin/index.html`），即可行内预览正文并一键批量通过发布！

---

## 三、命令参数详解

### 1. 爬取命令 (`crawl.bat` / `crawl.ps1`)

```powershell
# 基础爬取
.\crawl.bat https://example.com -o ./crawler/out/example/

# 限制深度 2 层、最多 50 页
.\crawl.bat https://example.com -o ./crawler/out/example/ --max-depth 2 --max-pages 50

# 礼貌慢速爬取（防封禁，每请求休眠 1.5 秒）
.\crawl.bat https://example.com -o ./crawler/out/example/ --delay 1.5

# 强制全量重爬（忽略断点缓存）
.\crawl.bat https://example.com -o ./crawler/out/example/ --force

# 指定正文选择器（覆盖自动提取）
.\crawl.bat https://docs.example.com -o ./crawler/out/docs/ --content-selector "main article"
```

| 参数 | 简写 | 默认值 | 描述 |
|---|---|---|---|
| `url` | - | *(必填)* | 目标起始 URL（种子链接） |
| `--output` | `-o` | *(必填)* | 输出目录路径 |
| `--max-depth` | - | `3` | 最大链接跟踪深度 |
| `--max-pages` | - | `200` | 最多爬取页面数 |
| `--delay` | - | `1.0` | 每次请求休眠秒数 |
| `--timeout` | - | `30` | 单次请求超时时间（秒） |
| `--content-selector` | - | `None` | 自定义 CSS 选择器 |
| `--force` | - | `False` | 强制全量重爬 |
| `--verbose` | `-v` | `False` | 打印 Debug 调试日志 |

---

### 2. 批量入库命令 (`ingest.bat` / `ingest.ps1`)

```powershell
# 基础导入
.\ingest.bat ./crawler/out/fmtbio/

# 自定义业务领域与分类
.\ingest.bat ./crawler/out/fmtbio/ --domain CS --category WEBSITE_KNOWLEDGE --doc-form text_model

# 包含列表页（默认会自动过滤列表页）
.\ingest.bat ./crawler/out/fmtbio/ --include-lists

# 自定义最小字数过滤与追加标签
.\ingest.bat ./crawler/out/fmtbio/ --min-chars 100 --tags fmtbio clinical_study
```

| 参数 | 默认值 | 描述 |
|---|---|---|
| `directory` | *(必填)* | 爬虫输出目录路径 |
| `--domain` | `CS` | 业务领域：`CS` / `SALES` / `DOCTOR` |
| `--category` | `WEBSITE_KNOWLEDGE` | 文档业务分类 |
| `--doc-form` | `text_model` | 知识库形态：`text_model`（长文档切块）/ `qa_model`（问答对生成） |
| `--min-chars` | `80` | 最小正文字数阈值（低于此字数自动跳过） |
| `--include-lists` | `False` | 是否包含分类主页与分页列表（默认自动过滤） |
| `--tags` | `None` | 追加的自定义标签列表 |

---

## 四、端到端数据流与架构设计

```
┌────────────────────────────────────────────────────────┐
│ 1. 目标网站 (https://fmtbio.com)                        │
└──────────────────────────┬─────────────────────────────┘
                           │  crawl.bat (BFS 广度爬取 + Trafilatura 提取)
                           ▼
┌────────────────────────────────────────────────────────┐
│ 2. 本地爬虫产物 (crawler/out/fmtbio/)                   │
│    ├── .crawl_state.json (断点缓存)                     │
│    ├── index.md (全站关系索引 & 外链表)                 │
│    └── hangye/1145.html.md (含 YAML Frontmatter)       │
└──────────────────────────┬─────────────────────────────┘
                           │  ingest.bat (列表机筛 + Frontmatter 剥离)
                           ▼
┌────────────────────────────────────────────────────────┐
│ 3. 数据库与资产存储 (MySQL & public/uploads/)           │
│    ├── core_knowledge_assets (记录 source_domain/url) │
│    ├── core_knowledge_documents (唯一逻辑文档)         │
│    └── core_knowledge_document_versions (状态: PENDING)│
└──────────────────────────┬─────────────────────────────┘
                           │  管理员在 Admin UI 预览并点击【一键批量通过】
                           ▼
┌────────────────────────────────────────────────────────┐
│ 4. Dify 知识库 RAG Pipeline                             │
│    ├── 自动以纯净正文 + 清洗后标题上传                 │
│    └── 向量化切块 (Embedding & Chunking)                │
└────────────────────────────────────────────────────────┘
```

---

## 五、数据库模型设计 (Alembic Migration)

Alembic 迁移脚本：`backend/migrations/versions/e2f3a4b5c6d7_add_asset_source_metadata.py`

### `core_knowledge_assets` 表扩展字段

| 字段名 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `source_type` | `VARCHAR(32)` | 来源类型（`CRAWLER` / `UPLOAD` / `ETL`），已加索引 | `'CRAWLER'` |
| `source_domain` | `VARCHAR(128)` | 站点主域名，已加索引 | `'fmtbio.com'` |
| `source_url` | `VARCHAR(1024)` | 原始网页 URL | `'https://fmtbio.com/hangye/1145.html'` |
| `extra_meta` | `JSON` | 扩展元数据（抓取深度、批次号、相对路径、主索引关联等） | `{"depth": 2, "batch_id": "crawl_fmtbio_20260818", "relative_path": "hangye/1145.html.md"}` |

---

## 六、关键算法与技术细节

### 1. 静态/动态网页自适应检测 (`detector.py`)
- 先以轻量 `requests` 请求 HTML；
- 计算 `<body>` 可见文本长度与 `<script>` 标签数量。当可见正文 $< 200$ 字符且包含多段脚本时，判定为动态渲染 SPA，自动唤起 `Playwright` Chromium 渲染真实 DOM。

### 2. YAML Frontmatter 解析与剥离 (`frontmatter.py`)
- 从爬取文件中解析 `url`, `title`, `crawled_at`, `depth` 存入数据库字段；
- **物理资产与发往 Dify 的文本中坚决剔除 YAML 头部**，杜绝 Dify 将抓取时间切入问答 Chunk 造成召回污染。

### 3. 分类列表与分页导航智能过滤 (`ingest.py`)
- **路径正则拦截**：跳过 `*/index.md`、`*/index_\d+.html.md`、`*/tags/*`、`*/category/*`；
- **行密度分析**：统计行长 $\le 20$ 字符的短行比例。若短行占比 $\ge 75\%$，自动识别为导航菜单列表壳并丢弃；
- **面包屑拦截**：正文过短且含“当前位置：首页 >”的占位页直接过滤。

### 4. URL 溯源与文章多版本流转
- 导入时依据 `source_url + source_domain` 匹配已有文档；
- **内容无变化**：SHA256 一致，自动跳过；
- **内容有更新**：在同一 `KnowledgeDocument` 下自动生成 `revision + 1` 的新版本（`PENDING`），审核通过后无缝替代线上旧版。

### 5. Dify 文件名清洗 (`dify_knowledge_client.py`)
- Dify 严格禁止 `name` 包含 `/ \ : * ? " < > |`；
- `sanitize_dify_filename()` 自动剥离路径斜杠，并将文章中文标题规范化为合法文件名推送给 Dify。

---

## 七、审核后台（Admin UI）功能与接口

- **访问地址**：`http://localhost:8000/public/admin/index.html`

### 核心功能
1. **行内就地下拉展开（Inline Dropdown Drawer）**：点击列表任意行，直接在其正下方下拉展示详情与预览，告别页面底部滚动。
2. **实时 Markdown 正文预览**：右侧面板秒级渲染纯净正文，显示原始网页直达链接与字数。
3. **一键批量通过并发布**：支持表头全选，点击 **【🚀 一键批量通过并发布 / 重试同步】** 异步驱动 Dify Pipeline 向量切块。

### 配套后端 API
- `GET /api/v1/admin/knowledge/document-versions/{id}/preview`：获取资产纯净文本供前端预览；
- `POST /api/v1/admin/knowledge/document-versions/batch-approve`：批量审核通过并安排 Dify 发布；
- `POST /api/v1/admin/knowledge/document-versions/batch-reject`：批量驳回待审版本。

---

## 八、项目文件结构

```
microbiome_ai_engine/
├── crawl.bat                 # 根目录爬取快捷脚本 (CMD)
├── crawl.ps1                 # 根目录爬取快捷脚本 (PowerShell)
├── ingest.bat                # 根目录批量入库快捷脚本 (CMD)
├── ingest.ps1                # 根目录批量入库快捷脚本 (PowerShell)
│
├── crawler/
│   ├── __init__.py           # 模块接口与延迟加载
│   ├── __main__.py           # python -m crawler 命令行入口
│   ├── cli.py                # 爬虫命令行参数解析
│   ├── config.py             # 爬虫配置数据类 (CrawlConfig)
│   ├── detector.py           # 静态/动态网页自适应检测
│   ├── fetcher.py            # 网页下载器 (requests + Playwright)
│   ├── extractor.py          # 正文智能提取 (trafilatura) 与链接解析
│   ├── frontmatter.py        # YAML Frontmatter 解析与剥离工具
│   ├── session.py            # 断点续爬状态管理器 (.crawl_state.json)
│   ├── crawler.py            # BFS 调度引擎
│   ├── writer.py             # Markdown 写入与 index.md 生成
│   ├── ingest.py             # 批量入库与列表智能机筛引擎
│   ├── requirements.txt      # 爬虫独立依赖
│   └── out/                  # 爬虫产物存储根目录
│
└── backend/
    ├── migrations/versions/  # Alembic 数据库迁移文件
    │   └── e2f3a4b5c6d7_add_asset_source_metadata.py
    ├── app/clients/
    │   └── dify_knowledge_client.py # Dify 知识库发布客户端 (带文件名清洗)
    ├── app/knowledge/        # 知识库后端服务、数据模型与路由
    └── public/admin/
        └── index.html        # 全新升级的审核后台 (行内就地预览 + 批量发布)
```

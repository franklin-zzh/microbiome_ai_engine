# 网页爬虫与知识库入库系统：全流程设计细节与关键决策汇总

> **文档说明**：本文档整理自项目全周期对话，完整记录了从需求提出、架构决策、数据模型设计、列表机筛算法、Dify 向量对接，到审批后台交互升级的全部关键细节与避坑总结。可直接复制用于项目归档或技术分享。

---

## 目录

1. [需求背景与核心挑战](#1-需求背景与核心挑战)
2. [架构决策与权衡分析（Decision Matrix）](#2-架构决策与权衡分析decision-matrix)
3. [数据库模型与元数据设计（Schema & Migration）](#3-数据库模型与元数据设计schema--migration)
4. [核心算法与技术实现](#4-核心算法与技术实现)
   - 4.1 静态/动态网页自适应探测
   - 4.2 正文提取与 Frontmatter 剥离策略
   - 4.3 智能列表与导航页机筛算法 (Anti-Nav Filter)
   - 4.4 URL 溯源与文章版本独立流转
   - 4.5 Dify 接口文件名合规性清洗
5. [审批管理后台（Admin UI）体验重构](#5-审批管理后台admin-ui体验重构)
6. [遇到的典型问题与修复方案（Troubleshooting）](#6-遇到的典型问题与修复方案troubleshooting)
7. [极简运维与使用指南](#7-极简运维与使用指南)

---

## 1. 需求背景与核心挑战

### 业务背景
微生态 AI 问答引擎需要持续扩充知识库。大量权威微生态资讯、临床研究及产品指南来源于官方网站（如 `fmtbio.com` 等）。原系统已有手工上传与销售案例 ETL，但缺乏**全自动站点深度爬取**与**结构化文档入库审批**的闭环能力。

### 核心挑战
1. **内容噪音大**：网站含有大量导航栏、侧边栏、免责声明、页脚版权、分类列表及分页导航，若直接丢给 Dify 切块，会导致垃圾 Chunk 充斥向量库。
2. **来源无法追溯**：原 `core_knowledge_assets` 仅记录物理文件名（如 `uploads/uuid.md`），无法留存原网页 URL、站点主域名及抓取时间。
3. **版本管理与全站目录关联**：如果后续原网站文章更新，需要能按 URL 自动识别并创建新版本；同时全站的 `index.md` 目录树需要留存但不能污染 Dify 检索。
4. **审核效率低**：原后台缺乏文档正文实时预览与一键批量通过能力，审核海量页面需逐一打开或下载。

---

## 2. 架构决策与权衡分析（Decision Matrix）

| 维度 | 决策方案 | 备选方案 | 决策理由 |
|---|---|---|---|
| **爬虫模块定位** | 独立于 `crawler/`，与 `etl/` 保持同级 | 混在 `etl/` 或 `backend/` 内部 | 爬虫与 Web 后端依赖解耦（Playwright 体积大），保持单职责。 |
| **页面渲染引擎** | `requests` 优先 + `Playwright` 自适应回退 | 全量使用 Playwright 或 Selenium | 静态页面使用 requests 速度提升 10~20 倍，仅对 SPA 单页应用唤起无头浏览器。 |
| **正文提取机制** | 本地 `trafilatura` 智能提取标题与正文 | 直接抓 HTML 丢给 Dify 处理 | 节约 40%~70% Dify Token 开销，杜绝页脚/导航污染向量检索。 |
| **Frontmatter 处理** | **爬取时保留，入库时剥离** | 直接留存在 Markdown 正文中 | 抓取阶段需要记录元数据；入库时元数据进入 MySQL 字段，纯净正文进 Dify，避免元数据被切成垃圾 QA。 |
| **全站 `index.md`** | **作为站点总览资产留存，不推 Dify** | 作为普通文档推入 Dify | `index.md` 包含大量全站关键词与链接，若推入 Dify 会在语义检索中严重稀释命中精准度。 |
| **目录页/分页列表** | **入库阶段多特征自动过滤** | 人工在后台逐一审阅删除 | 绝大部分 `keyan/index.md`、`zhishi/index_2.html` 纯属导航壳，机筛可减少 50%~80% 无效人工审核。 |
| **后台预览交互** | **表格行内就地下拉展开（Inline Drawer）** | 底部固定卡片或弹窗 Modal | 避免在 100+ 列表中来回大幅滚动到底部，原地点击展开查看正文与版本。 |

---

## 3. 数据库模型与元数据设计（Schema & Migration）

针对文档来源追溯需求，通过 Alembic 迁移脚本（`e2f3a4b5c6d7_add_asset_source_metadata.py`）对 `core_knowledge_assets` 进行了结构扩展：

```sql
ALTER TABLE mb_ai_engine.core_knowledge_assets
  ADD COLUMN source_type VARCHAR(32) NULL COMMENT '来源类型: CRAWLER / UPLOAD / ETL',
  ADD COLUMN source_domain VARCHAR(128) NULL COMMENT '来源主域名: 如 fmtbio.com',
  ADD COLUMN source_url VARCHAR(1024) NULL COMMENT '原始页面URL',
  ADD COLUMN extra_meta JSON NULL COMMENT '扩展元数据(深度/批次号/目录关联等)';

CREATE INDEX ix_core_knowledge_assets_source_type ON mb_ai_engine.core_knowledge_assets (source_type);
CREATE INDEX ix_core_knowledge_assets_source_domain ON mb_ai_engine.core_knowledge_assets (source_domain);
```

### 数据流映射关系

```
[原始网页: https://fmtbio.com/hangye/1145.html]
                  │
                  ▼
[crawler 产物: hangye/1145.html.md]
  ---
  url: "https://fmtbio.com/hangye/1145.html"
  title: "高净值人群需要定制肠道健康管理服务"
  crawled_at: 2026-08-18T02:20:42Z
  depth: 2
  ---
  # 高净值人群需要定制肠道健康管理服务
  人体微生态系统...
                  │
                  ▼ ingest.bat 剥离 Frontmatter
[数据库记录]
  • core_knowledge_assets:
      - source_type: 'CRAWLER'
      - source_domain: 'fmtbio.com'
      - source_url: 'https://fmtbio.com/hangye/1145.html'
      - extra_meta: {"batch_id": "crawl_fmtbio_20260818", "depth": 2, "index_asset_id": 10}
      - object_key: 'uploads/cf6e72ebfc364ea79a9cfd70c8907a36.md' (纯净正文)
  • core_knowledge_documents:
      - title: '高净值人群需要定制肠道健康管理服务'
      - tags: ['source:fmtbio.com', 'type:crawler', 'batch:crawl_fmtbio_20260818']
  • core_knowledge_document_versions:
      - revision: 1, status: 'PENDING'
                  │
                  ▼ 管理员在后台审批通过
[Dify 知识库 Dataset (text_model)]
  • 文件名: '高净值人群需要定制肠道健康管理服务.md'
  • 向量化分块: 仅基于纯净正文进行切分，无 YAML 头部与导航干扰
```

---

## 4. 核心算法与技术实现

### 4.1 静态/动态网页自适应探测 (`detector.py`)
```python
def is_dynamic_page(html_content: str) -> bool:
    soup = BeautifulSoup(html_content, "html.parser")
    # 移除脚本与样式后的可见文本
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    visible_text = soup.get_text().strip()
    scripts = soup.find_all("script")
    # 文本字数过短且脚本数量较多，判定为动态渲染 SPA
    return len(visible_text) < 200 and len(scripts) >= 2
```

### 4.2 正文提取与 Frontmatter 剥离策略 (`frontmatter.py`)
利用正则表达式精准分离 YAML 元数据与纯净正文：
```python
FRONTMATTER_PATTERN = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", re.DOTALL)

def parse_frontmatter(raw_text: str) -> ParsedMarkdown:
    match = FRONTMATTER_PATTERN.match(raw_text)
    if not match:
        return ParsedMarkdown(clean_content=raw_text.strip())
    meta_str, body = match.groups()
    meta = yaml.safe_load(meta_str) or {}
    return ParsedMarkdown(
        url=meta.get("url"),
        title=meta.get("title"),
        crawled_at=meta.get("crawled_at"),
        depth=meta.get("depth"),
        clean_content=body.strip(),
    )
```

### 4.3 智能列表与导航页机筛算法 (`ingest.py`)
为了解决 `keyan/index.md`、`zhishi/index_2.html.md` 等列表页占用审核精力的问题，设计了多维度的过滤算法：
```python
def _is_list_or_nav_page(rel_path: str, parsed: ParsedMarkdown) -> tuple[bool, str]:
    rel_lower = rel_path.lower()
    # 1. 路径特征：index.md, 分页 index_\d+.html, 聚合标签路径
    if re.search(r"(/|^)index(_\d+)?\.html?\.md$", rel_lower) or rel_lower.endswith("/index.md"):
        return True, "分类目录或分页列表页 (index/pagination)"
    if re.search(r"/(tags|category|list|search|tag)/", rel_lower):
        return True, "聚合标签/分类列表路径"

    # 2. 文本行密度分析：短行比例过高说明是菜单或链接列表
    lines = [line.strip() for line in parsed.clean_content.splitlines() if line.strip()]
    if len(lines) >= 5:
        short_lines = sum(1 for l in lines if len(l) <= 20)
        short_ratio = short_lines / len(lines)
        if short_ratio >= 0.75:
            return True, f"导航/目录特征明显 (短行占比 {short_ratio:.0%})"

    # 3. 面包屑占位壳：包含面包屑且总字符过短 (< 350 字)
    if ("当前位置" in parsed.clean_content or "首页 >" in parsed.clean_content) and len(parsed.clean_content) < 350:
        return True, "面包屑导航占位页 (无实质知识正文)"

    return False, ""
```

### 4.4 URL 溯源与文章版本独立流转
在 `ingest.py` 中，入库不再盲目新增文档，而是优先以 `source_url + source_domain` 查询已有文档：
* **无记录**：新建 `KnowledgeDocument` 及 `V1.0` 待审版本；
* **内容一致（SHA256 相同）**：自动跳过，保证幂等；
* **内容更新**：在原有 `KnowledgeDocument` 下新增 `V1.1` 待审版本。每个文档拥有独立版本链条，审核通过后替换旧版本在 Dify 上的投影。

### 4.5 Dify 接口文件名合规性清洗 (`dify_knowledge_client.py`)
Dify 知识库接口对 `name` 字段与 multipart 文件名有强校验，禁止包含 `/ \ : * ? " < > |`。
```python
def sanitize_dify_filename(filename: str) -> str:
    base = Path(filename).name
    clean = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', base).strip(' ._')
    return clean or "document.md"
```
发布时将文章真实中文标题（如 `高净值人群需要定制肠道健康管理服务.md`）作为显示文件名推入 Dify。

---

## 5. 审批管理后台（Admin UI）体验重构

在 [`backend/public/admin/index.html`](file:///d:/projects/microbiome_ai_engine/backend/public/admin/index.html) 中实现了三大核心升级：

1. **行内就地下拉展开（Inline Dropdown Drawer）**：
   - 用户在列表中点击任意文章行，直接在行下方无缝下拉滑出详情面板；
   - 彻底避免在 100+ 文档列表中来回翻滚到底部。
2. **Markdown 纯净正文即时预览**：
   - 右侧面板秒级请求 `/admin/knowledge/document-versions/{id}/preview` 加载纯净 Markdown；
   - 展示原始网页点击直达链接与字数。
3. **一键批量审核与多状态支持**：
   - 支持表头全选，对 `PENDING` 待审或 `APPROVED` 发布中状态的文档一键批量通过并同步推送到 Dify。

---

## 6. 遇到的典型问题与修复方案（Troubleshooting）

### 问题 1：Windows 控制台 GBK 编码报错 (`UnicodeEncodeError`)
* **现象**：CLI 打印 Emoji 图标（如 `🚀`、`📄`）时，Windows cmd 报错闪退。
* **修复**：将所有 CLI 输出前缀改为标准 ASCII 标签，如 `[START]`、`[DONE]`、`[WARN]`、`[TIP]`。

### 问题 2：Pydantic 序列化 500 报错 (`ValidationError`)
* **现象**：`GET /admin/knowledge/documents` 报 `Input should be a valid dictionary or instance of KnowledgeDocumentOut`。
* **修复**：在 `KnowledgeDocumentOut` 中补全 `model_config = ConfigDict(from_attributes=True)`。

### 问题 3：Dify 直传 400 报错 (`Filename contains invalid characters`)
* **现象**：一键发布到 Dify 时，Dify 返回 400 `Filename contains invalid characters`。
* **修复**：爬虫物理路径 `hangye/1145.html.md` 包含 `/`。使用 `sanitize_dify_filename` 清洗，并改用中文文章标题作为显示名。

### 问题 4：已审核状态下无法勾选全选
* **现象**：切换到“审核通过/发布中 (`APPROVED`)”筛选时，复选框呈置灰状态。
* **修复**：调整复选框渲染逻辑，根据当前筛选状态智能绑定对应版本 ID，支持全选及一键批量重试发布。

### 问题 5：Dify 异步向量化索引失败 (`Dify indexing did not complete: error`)
* **现象**：一键批量发布 100+ 文档时，后台轮询 Dify 报 `indexing_status: error`。
* **原因**：瞬时并发过大导致底层 Embedding 向量模型 API 触发 Rate Limit 速率限制或网络抖动超时。
* **修复**：
  1. 优化 `wait_pipeline_document_indexed()`，在 Dify 报错时提取底层具体原因（如 `Rate limit exceeded`）便于排查；
  2. 结合系统的幂等重试机制，重新点击【一键批量通过/重试同步】时会自动清理 Dify 损坏文档并无缝重新发布。

### 问题 6：微信客服问候语首次推送偶发跳过 (`OpenKfId` 缺失导致无法回复)
* **现象**：用户首次发送“你好”，后台生成了 `cs_chat_logs` 日志记录，但微信客服界面未收到回复，隔一分钟再发消息才恢复正常。
* **原因**：企微微信客服回调事件（`kf_msg_or_event`）的 XML 报文中，外层 `<OpenKfId>` 字段有时为空（仅携带 `<Token>`）。旧逻辑依赖外层 `open_kfid`，为空时触发 `_try_push` 的 `SKIPPED` 守卫跳过了回复推送。
* **修复**：
  1. `_sync_and_collect` 调整为只要有 `Token` 即可正常拉取消息列表；
  2. `_route_synced_msg` 与 `_handle_synced_event` 优先从拉取到的具体消息 `msg.get("open_kfid")` 中提取真实客服 ID，确保 `_try_push` 100% 获得合法 `open_kf_id` 主动推送回复。

---

## 7. 极简运维与使用指南

### 1. 爬取整站文档
```powershell
.\crawl.bat https://fmtbio.com -o ./crawler/out/fmtbio/ --max-depth 3 --max-pages 200
```

### 2. 批量导入后台
```powershell
.\ingest.bat ./crawler/out/fmtbio/ --domain CS --category WEBSITE_KNOWLEDGE
```

### 3. 后台审批发布
1. 启动后端服务：`uvicorn app.main:app --reload`；
2. 浏览器打开 `http://localhost:8000/public/admin/index.html`；
3. 点击行内直接预览正文，勾选表头，点击 **【🚀 一键批量通过并发布】**，全量文档即刻同步入库 Dify！

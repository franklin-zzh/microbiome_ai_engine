# 数据库迁移说明（Alembic）

本目录使用 [Alembic](https://alembic.sqlalchemy.org/) 管理 `mb_ai_engine` 单库的表结构变更。

> **核心原则**：所有表结构改动必须通过 Alembic 迁移脚本完成，禁止在应用启动时调用 `Base.metadata.create_all()`。

## 目录结构

```text
backend/migrations/
├── alembic.ini          # Alembic 配置（数据库 URL 由 env.py 动态解析）
├── env.py               # 迁移执行环境：加载模型、解析数据库连接串
├── script.py.mako       # 新迁移文件模板
├── versions/            # 迁移脚本目录
│   ├── 57710d3bdf73_init_db.py
│   └── 6073b7f19ee8_add_category_to_core_knowledge_items.py
└── README.md            # 本文件
```

## 数据库连接

`env.py` 中的 `get_url()` 按以下优先级解析数据库 URL：

1. 环境变量 `DATABASE_URL`（测试、部署覆盖时使用）
2. `app.core.config.get_settings().database_url`（本地开发默认值）

本地开发默认连接：

```text
mysql+pymysql://root:fumate@localhost:3306/mb_ai_engine?charset=utf8mb4
```

> 注意：`alembic.ini` 中的 `sqlalchemy.url` 被注释掉，避免密码入库，同时规避 `%` 被 `configparser` 插值的问题。

## 常用命令

以下命令均需在 `backend/` 目录下执行，并确保虚拟环境已激活。

### 应用最新迁移

```powershell
$env:DATABASE_URL="mysql+pymysql://root:fumate@localhost:3306/mb_ai_engine?charset=utf8mb4"
.venv\Scripts\python.exe -m alembic upgrade head
```

### 回退到上一版本

```powershell
.venv\Scripts\python.exe -m alembic downgrade -1
```

### 回退到初始状态

```powershell
.venv\Scripts\python.exe -m alembic downgrade base
```

### 查看当前版本

```powershell
.venv\Scripts\python.exe -m alembic current
```

### 查看迁移历史

```powershell
.venv\Scripts\python.exe -m alembic history --verbose
```

## 创建新迁移

### 1. 修改模型

在 `backend/app/<domain>/models.py` 中完成表/字段变更，确保所有模型模块已被 `env.py` 导入：

```python
from app.agent_cs import models as _cs_models       # noqa: E402,F401
from app.agent_sales import models as _sales_models # noqa: E402,F401
from app.knowledge import models as _knowledge_models # noqa: E402,F401
```

### 2. 自动生成迁移脚本

```powershell
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe_your_change"
```

生成的脚本位于 `migrations/versions/`，命名格式为 `<rev>_<slug>.py`。

### 3. 人工审查脚本

**必须**打开生成的迁移脚本，检查 `upgrade()` / `downgrade()` 是否符合预期，特别注意：

- 字段类型、长度、默认值是否正确
- 索引命名是否遵循 `ix_<table>_<column>` 规范
- 外键关系是否按设计保留或省略
- 是否需要补充原生 SQL（例如 `AFTER column` 等 Alembic 不直接支持的 MySQL 语法）

### 4. 应用并验证

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
```

建议同时执行测试确认无回归：

```powershell
.venv\Scripts\python.exe -m pytest tests/ -v
```

## 项目约定

- **单库**：所有业务表位于 `mb_ai_engine`，通过表名前缀区分领域：
  - `core_*`：知识库体系（`core_knowledge_items`、`core_unanswered_questions`、`core_sales_cases`）
  - `cs_*`：客服会话体系（`cs_chat_logs`、`cs_session_state`、`cs_leads_preview`）
- **无外键跨领域**：`core_*` 与 `cs_*` 之间不建立物理外键；同领域内部可保留外键。
- **类型比对**：`env.py` 中启用 `compare_type=True`，列类型变更会自动生成 `ALTER COLUMN`。
- **时间戳**：建表统一使用 `server_default=sa.text('now()')`。
- **测试库**：`conftest.py` 自动指向 `mb_ai_engine_test`，并在测试前执行 `alembic upgrade head` 建表。

## 故障排查

### `Can't locate revision identified by 'xxx'`

通常是因为数据库中的 `alembic_version` 表记录与本地迁移脚本不一致。处理步骤：

1. 确认当前数据库版本：`alembic current`
2. 确认迁移文件是否存在且未被手动删除
3. 如确认脚本丢失，可从 Git 恢复或重新 `stamp` 到一致状态

### 自动生成的迁移缺少字段类型变更

确保 `env.py` 中 `compare_type=True` 已启用；若仍有遗漏，可手动在迁移脚本中补充 `op.alter_column(...)`。

### MySQL 特定语法（如 `AFTER column`）未生成

Alembic/SQLAlchemy 通用层不保证生成 MySQL 专有语法。可在迁移脚本中使用 `op.execute("ALTER TABLE ...")` 补充，参考 `6073b7f19ee8_add_category_to_core_knowledge_items.py`。

## 参考

- [Alembic 官方文档](https://alembic.sqlalchemy.org/en/latest/)
- [SQLAlchemy 2.x 文档](https://docs.sqlalchemy.org/en/20/)
- `backend/AGENT.md` 中 §3.1 / §3.2 关于数据存储与架构设计的说明

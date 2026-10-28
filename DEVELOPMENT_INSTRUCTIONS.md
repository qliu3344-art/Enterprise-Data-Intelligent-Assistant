# 企业数据智能助手 — 运维与开发文档

> **项目周期：** 2025.11 - 2026.04  
> **当前状态：** 已完成开发，后端 8002 端口 + 前端 SPA 运行中

---

## 一、快速开始

### 1.1 环境要求

- Python 3.10+
- Node.js 18+（仅前端开发需要）
- PostgreSQL 16+（本项目在 PG 17 上验证）+ **pgvector 扩展**
- 需提前创建数据库 `data_processing_platform` 并 `CREATE EXTENSION vector`

### 1.2 安装依赖

```bash
pip install -r requirements.txt

# 前端（仅开发时需要）
cd frontend && npm install
```

### 1.3 配置 .env

在项目根目录创建 `.env`，至少配置以下两项：

```
DASHSCOPE_API_KEY=你的通义千问API密钥
PG_PASSWORD=你的PostgreSQL密码
```

主机 / 端口 / 用户名 / 库名的默认值在 `app/config.py` 的 `Settings` 里
（`localhost:5432` / `postgres` / `data_processing_platform`）。

### 1.4 初始化数据库

```bash
python scripts/init_db.py
```

该脚本会先 `CREATE EXTENSION IF NOT EXISTS vector` 再建表——向量列的 DDL
依赖 `vector` 类型，扩展后于 `create_all` 会报 `type "vector" does not exist`。

### 1.5 启动

**Windows — 双击 `start.bat`** 或终端执行：

```powershell
.\start.ps1
```

**手动启动（跨平台）：**

```bash
# 后端（内置前端 SPA，单端口即可）
# --loop 不能省，原因见文末「Agent Checkpointer」那条
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --loop app.main:selector_loop_factory
```

启动后访问：
- 前端页面：http://localhost:8002
- API 文档（Swagger）：http://localhost:8002/docs

### 1.6 前端开发模式

```bash
cd frontend
npm run dev          # Vite 开发服务器，支持 HMR
npm run build        # 构建到 frontend/dist，由后端 serve（SPA fallback）
```

---

## 二、项目业务全景

### 2.1 业务痛点

某中大型企业，考勤、销售、客户、运营 4 个部门各自维护数据，格式不统一：

| 部门 | 数据内容 | 原始格式 | 典型问题 |
|---|---|---|---|
| 考勤 | 员工出勤、请假、加班 | Excel (.xlsx) | 表头命名各异，日期格式不一致 |
| 销售 | 订单、流水、业绩 | CSV | 编码问题，分隔符不统一 |
| 客户 | 客户信息、合同、跟进 | MySQL 数据库 | 历史数据脏乱，字段含义不明 |
| 运营 | 月度报表、活动总结 | PDF（非结构化） | 无法直接读取，需人工录入 |

**核心矛盾：** 4 个部门数据合在一起才能做完整业务分析，但格式不统一导致汇总靠人工，每次需要 2-3 天。

### 2.2 业务流程

```
配置数据源(Excel/CSV/MySQL/PDF)
  → 触发自动化采集
  → [PDF] 大模型提取非结构化信息
  → 异构表头语义对齐（LLM 做字段映射）
  → 数据清洗 Pipeline：
     1. 重复记录合并
     2. 缺失值智能填充
     3. 业务异常值智能判定（IQR 初筛 + LLM 终判）
  → 标准化入库（PostgreSQL 统一 schema）
  → 多维度业务分析 & 导出
  → 自然语言智能查询（Agent + RAG 双引擎）
```

### 2.3 核心模块

| 模块 | 功能 | 核心创新 |
|---|---|---|
| 多源数据采集 | 4 类数据源自动化接入 | 统一 Connector 抽象层（策略模式） |
| 非结构化 PDF 提取 | LLM 提取 PDF 报表信息 | 通义千问语义理解 |
| 异构表头语义对齐 | 跨部门字段自动映射 | LLM 语义匹配替代人工配置 |
| 数据清洗 Pipeline | 去重→填充→异常判异 | IQR 统计初筛 + LLM 终判两阶段，LLM 失败降级 pending_review |
| 自然语言智能查询 | 中文提问查数据、查文档 | LangChain Agent + RAG 混合检索 |
| RAG 制度文档问答 | 企业内部制度文档检索 | BGE 向量化 + BM25 混合检索 + 引用溯源 |

---

## 三、技术架构

### 3.1 技术栈

| 层 | 技术 | 用途 |
|---|---|---|
| Web 框架 | FastAPI | 异步高性能，自动生成 Swagger 文档 |
| ORM / 驱动 | SQLAlchemy 2.0 + psycopg 3 | PostgreSQL 连接与批量操作 |
| 数据处理 | Pandas + NumPy | 数据清洗和转换 |
| LLM | 通义千问 qwen-flash（DashScope） | 表头对齐、异常判定、PDF 提取、Agent、RAG 答案生成 |
| Agent 框架 | LangChain 1.x + LangGraph | ReAct Agent + Postgres Checkpointer（AsyncPostgresSaver） |
| 向量检索 | pgvector + sentence-transformers (BGE) | RAG 文档向量化与检索（与业务数据同库同表空间） |
| 关键词检索 | rank-bm25 | BM25 混合检索 |
| PDF 提取 | pdfplumber | 文本型 PDF 文本提取 |
| Excel | openpyxl | .xlsx 读写 |
| 前端 | Vue 3 + Vite + TypeScript | SPA |
| UI | Element Plus + ECharts + Tailwind CSS v4 | 组件库 + 图表 + 样式 |

### 3.2 整体架构图

```
┌──────────────────────────────────────────────────────────────────┐
│                        FastAPI REST API                          │
├──────────────────────────────────────────────────────────────────┤
│  Routers (6):                                                     │
│  datasource │ collect │ clean │ analysis │ query │ rag           │
├──────────────────────────────────────────────────────────────────┤
│  Services:                                                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────────────┐ │
│  │Connectors│ │ Aligner  │ │ Cleaner  │ │     Analyzer        │ │
│  │ Excel    │ │ LLM 语义  │ │ 去重     │ │  统计汇总 / 趋势     │ │
│  │ CSV      │ │ 字段映射  │ │ 填缺失   │ │  数据质量 / 导出     │ │
│  │ MySQL    │ │          │ │ 判异常   │ │                     │ │
│  │ PDF+LLM  │ │          │ │(IQR+LLM) │ │                     │ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────────────────┘ │
│  ┌──────────────────┐ ┌────────────────────────────────────────┐ │
│  │  Intent Router   │ │         RAG Pipeline (6 files)         │ │
│  │  LLM 意图分类     │ │  Loader → Chunker → Embedder          │ │
│  │  data/doc/hybrid │ │  → VectorStore → Retriever → Service  │ │
│  └──────────────────┘ │  (BGE + pgvector + BM25 混合检索)      │ │
│  ┌──────────────────┐ └────────────────────────────────────────┘ │
│  │   Query Agent    │                                            │
│  │ LangChain ReAct  │                                            │
│  │ + PostgresSaver  │                                            │
│  └──────────────────┘                                            │
├──────────────────────────────────────────────────────────────────┤
│  Database: PostgreSQL 17 + pgvector (SQLAlchemy ORM)             │
│  业务表: data_sources / raw_records / cleaned_records /          │
│          pipeline_logs / schema_mappings / chat_history /        │
│          query_trace / feedback                                  │
│  向量表: doc_chunks（制度条款+向量）/ doc_index_state /          │
│          business_conventions（业务口径）                        │
│  ★ 向量与业务数据同库同事务，不另起一套向量存储                   │
├──────────────────────────────────────────────────────────────────┤
│  Frontend: Vue 3 SPA (Vite + Element Plus + ECharts)             │
│  编译为静态文件，由 FastAPI 直接 serve（单端口部署）               │
└──────────────────────────────────────────────────────────────────┘
```

---

## 四、完整项目结构

```
企业数据智能助手/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI 入口，注册 7 个 router
│   ├── config.py                  # 配置管理（.env → Settings）
│   ├── database.py                # SQLAlchemy engine + session
│   ├── exceptions.py              # 全局异常定义
│   ├── logger.py                  # 日志系统
│   ├── retry.py                   # LLM 调用容错重试（指数退避 + 可重试分类）
│   ├── models/
│   │   ├── __init__.py
│   │   ├── chat_history.py        # 对话历史表
│   │   ├── query_trace.py         # 查询 Trace 表（可观测性）
│   │   ├── feedback.py            # 用户反馈表（点赞/点踩，数据飞轮的地基）
│   │   ├── cleaned_record.py      # 清洗后统一记录表
│   │   ├── datasource.py          # 数据源配置表
│   │   ├── pipeline_log.py        # 清洗流水日志表
│   │   ├── raw_record.py          # 原始采集记录表
│   │   ├── schema_mapping.py      # 表头映射配置表
│   │   ├── doc_chunk.py           # 制度条款 + 向量（pgvector）+ 文档级索引状态
│   │   └── business_convention.py # 业务口径长期记忆（pgvector）
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── analysis.py            # 分析 Pydantic
│   │   ├── clean.py               # 清洗 Pydantic
│   │   ├── collect.py             # 采集 Pydantic
│   │   ├── common.py              # 统一响应模型
│   │   └── datasource.py          # 数据源 Pydantic
│   ├── services/
│   │   ├── __init__.py
│   │   ├── aligner.py             # 异构表头语义对齐（LLM）
│   │   ├── analyzer.py            # 业务分析服务
│   │   ├── cleaner.py             # 数据清洗 Pipeline（IQR + LLM + pending_review 降级）
│   │   ├── collector.py           # 采集编排服务
│   │   ├── context_manager.py     # 上下文压缩（滑动窗口 + LLM 摘要）
│   │   ├── data_query_ops.py      # 数据查询核心逻辑（协议无关，Agent / MCP 复用）
│   │   ├── intent_router.py       # LLM 意图路由（data/doc/hybrid）
│   │   ├── memory_store.py        # 业务口径长期记忆（pgvector + 三层作用域 + 分池淘汰）
│   │   ├── query_agent.py         # LangChain ReAct Agent + Checkpointer
│   │   ├── connectors/
│   │   │   ├── __init__.py
│   │   │   ├── base.py            # 抽象基类 BaseConnector
│   │   │   ├── csv_conn.py        # CSVConnector
│   │   │   ├── excel.py           # ExcelConnector
│   │   │   ├── mysql_conn.py      # MySQLConnector
│   │   │   └── pdf.py             # PDFConnector（LLM 驱动）
│   │   ├── rag/
│   │   │   ├── __init__.py
│   │   │   ├── chunker.py         # 文档切分
│   │   │   ├── document_loader.py # 多格式文档加载
│   │   │   ├── embedder.py        # BGE 向量化
│   │   │   ├── retriever.py       # 混合检索（向量 + BM25）
│   │   │   ├── service.py         # RAG 答案生成 + 引用溯源
│   │   │   └── vector_store.py    # pgvector 索引管理（单事务增量重建）
│   │   └── skills/
│   │       ├── __init__.py
│   │       ├── base.py            # Skill 抽象（元数据 + handler）
│   │       ├── loader.py          # SKILL.md 扫描 + frontmatter 解析 + 校验
│   │       ├── registry.py        # 定义 × handler 合成注册表（intent → Skill）
│   │       ├── definitions/       # 每个能力一个目录，SKILL.md 存元数据 + 操作手册
│   │       ├── data_query.py      # 结构化数据查询 Skill
│   │       ├── doc_query.py       # 制度文档问答 Skill（RAG）
│   │       └── hybrid.py          # 数据 + 文档融合回答 Skill
│   └── routers/
│       ├── __init__.py
│       ├── analysis.py            # 分析报表 API
│       ├── clean.py               # 清洗管理 API
│       ├── collect.py             # 采集触发 API
│       ├── datasource.py          # 数据源管理 API
│       ├── feedback.py            # 用户反馈 API（点赞/点踩）
│       ├── query.py               # 自然语言查询 API（三路路由 + SSE 流式）
│       └── rag.py                 # RAG 制度问答 API
├── frontend/
│   ├── package.json               # Vue 3 + Element Plus + ECharts + Tailwind v4
│   ├── vite.config.ts
│   ├── src/
│   │   ├── main.ts
│   │   ├── App.vue
│   │   ├── api/                   # Axios 请求封装（analysis/clean/collect/datasource/feedback/query/rag）
│   │   ├── components/
│   │   │   ├── analysis/TrendChart.vue
│   │   │   ├── common/StatusTag.vue
│   │   │   └── layout/AppHeader.vue, AppSidebar.vue
│   │   ├── composables/           # VueUse 组合式函数
│   │   ├── pages/                 # 10 个页面组件
│   │   ├── router/index.ts        # Vue Router
│   │   └── types/                 # TypeScript 类型定义
│   └── env.d.ts
├── scripts/
│   ├── init_db.py                 # 建库建表
│   ├── setup_db.py                # 一键建库 + 采集 + 清洗（端到端验证）
│   ├── generate_mock_data.py      # 生成模拟多源数据
│   ├── verify_e2e.py              # 端到端验证（TestClient，不走 HTTP）
│   ├── test_full_interaction.py   # 全 API 端点交互测试
│   ├── test_rag.py                # RAG 全链路测试（索引 → 检索 → 问答）
│   ├── test_json_convert.py       # _json_to_text 转换测试
│   ├── expand_dataset.py          # 评估数据集自动扩充（LLM 生成同义变体）
│   ├── check_dataset_quality.py   # 评估数据集质检（漂移/重复/过短）
│   ├── backfill_keywords.py       # 回填评测集缺失的 expected_doc_keywords
│   └── sync_feedback_to_dataset.py # 点踩反馈 → 评测集候选样本
├── data/
│   ├── uploads/                   # 上传的原始文件
│   └── exports/                   # 导出的分析报告
├── tests/
│   ├── __init__.py
│   └── conftest.py
├── eval.py                        # 四维度 Agent 评估脚本
├── eval_dataset.json              # 评估数据集
├── eval_report_*.json             # 评估报告（历史记录）
├── logs/                          # 日志文件目录
├── requirements.txt
├── README.md
├── start.bat                      # Windows 一键启动（CMD）
├── start.ps1                      # Windows 一键启动（PowerShell）
├── .env                           # 环境变量（需自行创建）
└── .gitignore
```

---

## 五、核心模块说明

### 5.1 智能查询引擎（query_agent.py + intent_router.py）

用户用自然语言提问，系统自动判断意图并路由：

```
用户提问 → Intent Router（读 logprobs 真实分布 → p_top / label_mass / margin）
  │         p_top < 0.99 时不走单引擎，升级为 hybrid 安全网
  ├── data_query → LangChain ReAct Agent → SQL 查询 → 结构化数据回答
  ├── doc_query  → RAG Pipeline → 文档检索 → 引用溯源回答
  └── hybrid     → 两边并行 → LLM 合成统一答案（SSE 流式返回）
```

- **Intent Router** (`intent_router.py`)：不读模型自报的 confidence，直接读首 token 的 logprobs 分布。
  - 标签写成单 token 的 `数`/`文`/`混`（`tokenizer.encode("data_query")` 是 2 个 token，多 token 标签只能拿到首 token 处的边缘概率，且不报错）
  - `max_tokens=1` + `temperature=1` + `top_logprobs=5`（写死常量，合法区间 `[0,5]`，越界 HTTP 400）
  - 派生量：`p_top`（阈值主指标）、`label_mass`（prompt 约束健康度）、`margin`；**绝不重新归一化**，否则会抹掉 `label_mass` 这个信号
  - 阈值 0.99：**下界是结构性的**（必须 > `RULE_CONFIDENCE_CAP` 0.70，否则规则路径的高置信分支会被直接放行）；**取值由 692 条实测扫出**——扫「每消掉 1 条错误答案要付几次无谓升级」，0.75→0.99 是 4~7.5，0.99 以上翻倍再翻倍，取便宜区末端
  - 降级链 L0–L3：启动时校验标签单 token（fail fast）→ 重试（预算由端到端 P99 反推）→ 规则降级（匹配**用户问题**，confidence 被常量卡在阈值下）→ 兜底与熔断
  - 每次路由落一条结构化日志（`path` / `degrade` / `top5` 原始分布），供离线重调阈值
- **Query Agent** (`query_agent.py`)：LangChain 1.x `create_agent` + `@tool` 装饰器定义工具 + `AsyncPostgresSaver` Checkpointer 持久化对话（独立的 `agent_checkpoints` 库，与业务库分开放）
- **路由入口** (`routers/query.py`)：三路并发 + SSE 流式响应；意图路由用 `asyncio.to_thread` 包一层，避免同步 LLM 调用阻塞事件循环

### 5.2 RAG 管线（services/rag/）

6 个文件组成完整管线：

| 文件 | 职责 |
|---|---|
| `document_loader.py` | 加载 .txt/.md/.pdf 等多格式文档 |
| `chunker.py` | 文档切分为语义块 |
| `embedder.py` | BGE 模型向量化 |
| `vector_store.py` | pgvector 索引管理（单事务增量重建、内容哈希复用向量、统计） |
| `retriever.py` | 向量检索 + BM25 关键词检索 → 混合排序 |
| `service.py` | 构建 Prompt → LLM 生成答案 → 返回 `{answer, sources}` |

### 5.3 数据清洗 Pipeline（cleaner.py）

三步骤顺序执行，每一步记录日志到 `pipeline_logs` 表：

1. **去重** — 自动识别主键列组合（员工ID+日期等），`drop_duplicates`
2. **填充** — 数值列中位数填充，分类列众数填充，日期列不填充
3. **异常判异** — IQR 统计初筛 → LLM 终判（结合业务上下文）。候选按偏离度降序排列，排序只决定「先看谁」、不决定「看谁」，所以不做数量截断；按单批容量（`CANDIDATE_BATCH_SIZE = 30`）**分批全量送审**，覆盖百分之百。LLM 调用失败时该批自动降级，候选数据标记为 `pending_review` 待人工审核，不丢弃；分批之后单批失败不影响其余批次

### 5.4 Connector 策略模式（services/connectors/）

| Connector | 数据源 | 关键实现 |
|---|---|---|
| `ExcelConnector` | .xlsx/.xls | openpyxl，支持指定 sheet、跳过表头行 |
| `CSVConnector` | .csv | 自动检测编码和分隔符 |
| `MySQLConnector` | 远程数据库 | PyMySQL，执行自定义 SQL |
| `PDFConnector` | .pdf | pdfplumber 提取文本 → LLM 结构化 |

### 5.5 评估体系（eval.py）

四维度评测，权重可配：

| 维度 | 权重 | 说明 |
|---|---|---|
| 意图分类准确率 | 25% | `classify_intent` 是否匹配 `expected_intent` |
| 工具选择准确率 | 25% | Agent 是否调用了预期工具 |
| RAG 检索命中率 | 15% | 关键词命中（基线）+ LLM 语义相关性（主指标） |
| 答案质量 LLM 评分 | 35% | LLM-as-Judge 对最终答案打 1-5 分 |

```bash
python eval.py                      # 全量评估
python eval.py --category data_query # 只测某类
python eval.py --skip-agent          # 只测意图分类 + LLM Judge（无需数据库）
```

---

## 六、数据库设计

### 6.1 数据源配置表 `data_sources`

> **本节 DDL 是 PostgreSQL 语法**（旧版是 MySQL 语法，已随存储迁移更新）。
> **ORM 模型才是唯一事实源** —— 实际建表走 `python scripts/init_db.py`，
> 下面的 DDL 仅供快速了解表结构，不要手工执行后当作与代码一致。
>
> MySQL → PostgreSQL 的几处语法差异，看 DDL 时留意：
> - `AUTO_INCREMENT` → `SERIAL` / `BIGSERIAL`
> - 行内 `ENUM(...)` → 独立的 `CREATE TYPE ... AS ENUM (...)`（PG 里是**真实类型对象**，
>   `drop_all` 不会连带删除，测试里反复 create/drop 会撞 `type already exists`）
> - 行内 `COMMENT '...'` → 独立的 `COMMENT ON COLUMN ... IS '...'`（PG 不接受行内写法）
> - `DATETIME` → `TIMESTAMP`；`ON UPDATE CURRENT_TIMESTAMP` → 无对应，由 SQLAlchemy 的
>   `onupdate` 在 Python 侧维护
> - `ENGINE=InnoDB CHARSET=utf8mb4` → 不需要，PG 的库级编码在建库时定
> - `INDEX idx_x (col)` 写在 `CREATE TABLE` 里 → PG 是独立的 `CREATE INDEX`

```sql
CREATE TYPE source_type_enum AS ENUM ('excel','csv','mysql','pdf');
CREATE TYPE datasource_status_enum AS ENUM ('active','inactive','error');

CREATE TABLE data_sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    source_type source_type_enum NOT NULL,
    file_path VARCHAR(500),
    db_host VARCHAR(200),
    db_port INT DEFAULT 3306,
    db_name VARCHAR(100),
    db_user VARCHAR(100),
    db_password VARCHAR(200),
    db_query VARCHAR(1000),
    sheet_name VARCHAR(100),
    delimiter VARCHAR(10) DEFAULT ',',
    encoding VARCHAR(20) DEFAULT 'utf-8',
    skip_rows INT DEFAULT 0,
    status datasource_status_enum DEFAULT 'active',
    last_collect_at TIMESTAMP,
    last_collect_count INT DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
```

### 6.2 原始采集记录表 `raw_records`

```sql
CREATE TYPE raw_record_status_enum AS ENUM ('raw','aligned','cleaned','error');

CREATE TABLE raw_records (
    id SERIAL PRIMARY KEY,
    source_id INT NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    batch_id VARCHAR(50) NOT NULL,
    raw_data JSON NOT NULL,          -- 原始数据（保留所有字段，不做清洗）
    source_row_index INT,            -- 在原文件中的行号
    status raw_record_status_enum DEFAULT 'raw',
    error_info TEXT,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_source_id ON raw_records (source_id);
CREATE INDEX idx_batch_id  ON raw_records (batch_id);
CREATE INDEX idx_status    ON raw_records (status);
```

### 6.3 清洗后统一记录表 `cleaned_records`

```sql
CREATE TABLE cleaned_records (
    id SERIAL PRIMARY KEY,
    batch_id VARCHAR(50) NOT NULL,
    source_id INT NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    employee_name VARCHAR(100),
    employee_id VARCHAR(50),
    department VARCHAR(100),
    data_type VARCHAR(50),           -- attendance/sales/customer/operation
    record_date DATE,
    business_data JSON NOT NULL,     -- 标准化后的业务字段
    quality_score FLOAT DEFAULT 1.0,
    is_anomaly BOOLEAN DEFAULT FALSE,
    anomaly_reason TEXT,
    anomaly_status VARCHAR(30) DEFAULT 'normal',  -- normal/pending_review/confirmed
    pending_check_fields JSON,       -- 待审核字段（LLM 降级时保留的 IQR 候选信息）
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_batch_id    ON cleaned_records (batch_id);
CREATE INDEX idx_data_type   ON cleaned_records (data_type);
CREATE INDEX idx_record_date ON cleaned_records (record_date);
```

### 6.4 表头映射配置表 `schema_mappings`

```sql
CREATE TABLE schema_mappings (
    id SERIAL PRIMARY KEY,
    source_id INT NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    mapping_data JSON NOT NULL,      -- 原始表头 → 标准字段映射
    is_llm_generated BOOLEAN DEFAULT TRUE,
    confidence FLOAT,
    manual_reviewed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT now()
);
```

> `is_llm_generated` / `manual_reviewed` 语义是布尔，列类型也是 **`BOOLEAN`**。
> 迁移前 ORM 里写的是 `Integer`（MySQL 会静默把 `False` 存成 0），
> PostgreSQL 不接受往 integer 列写 `False`，已一并修正。

### 6.5 清洗流水日志表 `pipeline_logs`

```sql
CREATE TABLE pipeline_logs (
    id SERIAL PRIMARY KEY,
    batch_id VARCHAR(50) NOT NULL,
    step_name VARCHAR(50) NOT NULL,  -- dedup/fill_missing/anomaly_detect
    input_count INT DEFAULT 0,
    output_count INT DEFAULT 0,
    affected_count INT DEFAULT 0,
    details JSON,                    -- 清洗规则及 LLM 判断详情
    duration_seconds FLOAT,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_pipeline_batch_id ON pipeline_logs (batch_id);
```

### 6.6 制度条款向量表 `doc_chunks`（pgvector）

```sql
CREATE EXTENSION IF NOT EXISTS vector;   -- 必须先于建表，否则 vector 类型不存在

CREATE TABLE doc_chunks (
    id             BIGSERIAL PRIMARY KEY,
    file_name      VARCHAR(255) NOT NULL,      -- 源文件名
    chunk_index    INT NOT NULL,               -- 文档内序号，从 0 起
    doc_title      VARCHAR(255) NOT NULL,
    chapter        VARCHAR(255) DEFAULT '',    -- ## 二级标题
    section        VARCHAR(255) DEFAULT '',    -- ### 三级标题
    content        TEXT NOT NULL,
    content_hash   VARCHAR(32) NOT NULL,       -- content 的 md5，增量重建先比哈希
    embedding      vector(512) NOT NULL,       -- BGE-small-zh-v1.5，已 L2 归一化
    effective_date DATE,                       -- NULL = 长期有效（不是「未生效」）
    created_at     TIMESTAMP DEFAULT now(),
    updated_at     TIMESTAMP DEFAULT now(),
    CONSTRAINT uq_doc_chunks_file_chunk UNIQUE (file_name, chunk_index)
);
CREATE INDEX idx_doc_chunks_doc_title      ON doc_chunks (doc_title);
CREATE INDEX idx_doc_chunks_effective_date ON doc_chunks (effective_date);
-- 故意不建向量索引：这个体量下顺序扫描是毫秒级且召回 100%。
-- 要加时 opclass 必须是 vector_cosine_ops，否则匹配不上 <=> 算子、静默退化成全扫。
```

### 6.7 文档索引状态表 `doc_index_state`

```sql
CREATE TABLE doc_index_state (
    file_name    VARCHAR(255) PRIMARY KEY,
    content_hash VARCHAR(32) NOT NULL,   -- 整份文件（含 frontmatter 表格）的 md5
    indexed_at   TIMESTAMP DEFAULT now()
);
```

> 这份状态**不能从 `doc_chunks` 推导**：文档级哈希覆盖含 frontmatter 的整份文件，
> 而 frontmatter 在分块前就被剥掉、不出现在任何 chunk 里。它替代的是旧版那个
> `data/chroma_db/.docs_checksum` 边车文件——放进同一事务是必要的，否则增量重建
> 中途失败会留下「向量已删、checksum 还是旧的」，下次启动据此判定「没变」，
> 这份文档从此再也不会被索引。

### 6.8 业务口径表 `business_conventions`（pgvector）

```sql
CREATE TABLE business_conventions (
    id              SERIAL PRIMARY KEY,
    content         TEXT NOT NULL,
    embedding       vector(512) NOT NULL,
    scope           VARCHAR(16) NOT NULL DEFAULT 'global',     -- global 公司级 / department 部门级
    department      VARCHAR(64),                               -- scope=department 时必填
    status          VARCHAR(16) NOT NULL DEFAULT 'candidate',  -- candidate 候选 / active 已生效
    session_id      VARCHAR(64),                               -- 候选口径的来源会话
    effective_from  DATE,                                      -- 生效起始日；NULL=不设下界
    effective_until DATE,                                      -- 失效日；NULL=仍是当前版本
    metadata        JSONB NOT NULL DEFAULT '{}',               -- 调用方附加字段，结构自由
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),        -- 本版本改写时间
    last_accessed   TIMESTAMPTZ NOT NULL DEFAULT now()         -- 部门口径/候选池的 LRU 依据
);
CREATE INDEX idx_conventions_last_accessed ON business_conventions (last_accessed);
CREATE INDEX idx_conventions_scope_dept    ON business_conventions (scope, department);
CREATE INDEX idx_conventions_status        ON business_conventions (status);
```

> ⚠️ 列名是 `metadata`（对外契约），但 ORM 属性名必须叫 `meta` ——
> `metadata` 在 SQLAlchemy 声明式基类上是保留名，直接用会抛 `InvalidRequestError`。
>
> 三个时间戳独立成列、而不是塞进 `metadata`：这样「改口」的 UPDATE 根本不碰
> `metadata`，调用方写入的字段不可能被静默抹掉。
>
> **`scope` / `department` 是一等列，不是 `metadata` 里的自由字段。** 召回要按它们
> 过滤、淘汰要按它们分组分片，藏在 JSONB 里这两件事都做不了，只能把整表拉进 Python
> 再筛。旧实现把「业务口径」当成一种东西、用一个全局池管，会话级临时限定撑满池子、
> 部门规则串到别的部门、一年用一次的重要口径被 LRU 淘汰——这三条都是建模问题，
> 调参数救不回来。
>
> **`effective_from` / `effective_until` 是口径的版本边界。** 口径会变
> （「2026 年起加班口径从 3 小时改成 2 小时」），这时不是删旧口径，是给旧的设终止
> 日期、再起一个新版本：企业数据问答里大量是历史问题，去年 11 月的异常率要用去年
> 11 月的口径算，覆盖掉就永久还原不出来。
>
> **没有「200 条全局上限」这个东西了。** 配额按作用域分片：正式口径不设条数上限
> （它由治理流程管、只有几十条量级），部门口径按部门配额超出时降级为候选，
> 候选池有自己的配额和停滞期。上限管在正式口径上，就是「年终奖口径必然被天天
> 出现的琐碎口径挤掉」。

---

## 七、API 接口一览

### 7.1 数据源管理 — `/api/v1/datasources`（`routers/datasource.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/datasources` | 注册数据源 |
| GET | `/api/v1/datasources` | 数据源列表 |
| GET | `/api/v1/datasources/{id}` | 数据源详情 |
| PUT | `/api/v1/datasources/{id}` | 更新数据源配置 |
| DELETE | `/api/v1/datasources/{id}` | 删除数据源 |
| POST | `/api/v1/datasources/{id}/test` | 测试连接 |
| POST | `/api/v1/datasources/upload` | 上传数据文件 |

### 7.2 数据采集 — `/api/v1/collect`（`routers/collect.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/collect/{source_id}` | 单源采集 |
| POST | `/api/v1/collect/batch` | 批量采集 |
| GET | `/api/v1/collect/history` | 采集历史 |
| GET | `/api/v1/collect/{batch_id}` | 某批次详情 |

### 7.3 数据清洗 — `/api/v1/clean`（`routers/clean.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/clean/{batch_id}` | 对某批次执行清洗 |
| GET | `/api/v1/clean/{batch_id}/logs` | 清洗日志 |
| GET | `/api/v1/clean/anomalies` | 查询异常记录 |
| PUT | `/api/v1/clean/anomalies/{id}` | 人工修正异常标记 |

### 7.4 业务分析 — `/api/v1/analysis`（`routers/analysis.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/analysis/dashboard` | 综合仪表盘（单端点，前端一次请求获取全量） |
| GET | `/api/v1/analysis/summary` | 汇总统计 |
| GET | `/api/v1/analysis/trend` | 趋势分析 |
| GET | `/api/v1/analysis/quality` | 数据质量报告 |
| GET | `/api/v1/analysis/export` | 导出分析报告（Excel） |

### 7.5 自然语言查询 — `/api/v1/query`（`routers/query.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/query` | 自然语言查询（SSE 流式返回） |
| GET | `/api/v1/query/history` | 查询历史 |

### 7.6 RAG 制度问答 — `/api/v1/rag`（`routers/rag.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/rag/ask` | 制度文档问答 |
| POST | `/api/v1/rag/reindex` | 重建索引 |
| GET | `/api/v1/rag/stats` | 索引统计 |

---

## 八、配置管理

`.env` 文件支持的配置项：

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `DASHSCOPE_API_KEY` | 是 | — | 通义千问 API 密钥 |
| `PG_PASSWORD` | 是 | — | PostgreSQL 密码 |
| `PG_HOST` | 否 | `localhost` | PostgreSQL 主机 |
| `PG_PORT` | 否 | `5432` | PostgreSQL 端口 |
| `PG_USER` | 否 | `postgres` | PostgreSQL 用户名 |
| `PG_DB` | 否 | `data_processing_platform` | 数据库名 |
| `LLM_MODEL` | 否 | `qwen-flash` | LLM 模型名 |

> 目前只有 `PG_PASSWORD` 和 `DASHSCOPE_API_KEY` 走环境变量，其余项在
> `app/config.py` 的 `Settings` 里写默认值（保持与迁移前一致的形态）。
> 口令拼进连接串前会 `quote_plus` 转义——含 `@` `:` `/` 等 URL 保留字符时，
> 不转义会把连接串拆错。

实际代码见 `app/config.py` 中的 `Settings` 类。

---

## 九、关键注意事项

1. **LLM 调用成本控制**：表头对齐每个数据源只调用一次并缓存；异常判异只送 IQR 筛选后的候选，候选按单批容量分批全量送审、不做数量截断（候选只占总数据量的千分之一量级，注意力容量才是分批的理由，成本不是）。
2. **不允许完全依赖 LLM**：LLM 判异结果是辅助性的，用户可人工覆盖。最终决定权在业务人员。LLM 调用失败时，IQR 候选自动降级为 `pending_review` 状态保留，不做丢弃。
3. **原始数据不可覆盖**：`raw_records` 表保留原始 JSON，清洗和标准化都在派生表上进行。
4. **PDF 提取的局限性**：纯扫描件 PDF 需要额外的 OCR（PaddleOCR/Tesseract），当前架构未集成。首期只支持文本型 PDF。
5. **数据源口令安全**：`data_sources.db_password` 目前是明文存储（那是**用户配置的远程业务库**口令，与应用自身存储无关）。生产环境建议使用 `cryptography` 库的 Fernet 对称加密存储。
6. **数据库驱动**：应用自身存储用 **psycopg 3**（`postgresql+psycopg://`，不是 `psycopg2`）。`MySQLConnector` 仍用 PyMySQL —— 那是四类接入源之一（读业务部门自己的 MySQL 库），与应用存储无关，刻意保留。
7. **前端部署**：前端编译为静态文件后由 FastAPI 直接 serve，单端口 8002 即可访问完整应用（无需 Nginx 反代或独立前端服务器）。
8. **Agent Checkpointer**：使用 LangGraph `AsyncPostgresSaver` 持久化到 **Postgres 的独立库 `agent_checkpoints`**（与业务库分开：checkpoint 是每轮都写的高频小事务，业务查询是慢查询，同库会互相拖累）。不用 SQLite —— 它是库级写锁，几百人并发时写入排队会抛 `database is locked`。
   **连不上就起不来，没有降级**：Postgres 在本项目里是硬依赖（业务数据、向量、会话检查点三样都在里面），「PG 挂了但应用还能用」这个状态不存在。回退 `MemorySaver` 换来的不是「降级可用」而是「接口照常 200、日志干净、只有历史悄悄不落盘」，所以 `main.py::on_startup` 会预热 Agent，失败即启动失败。

   **Windows 注意**：psycopg 的异步模式不支持默认的 `ProactorEventLoop`，而 uvicorn 在 win32 上是绕过全局策略显式选 Proactor 的，所以启动必须带 `--loop app.main:selector_loop_factory`（`start.ps1` 已带），漏了会**开不了机**（`_create_checkpointer` 直接判循环类型并给出指明成因的错误）。`eval.py` 那边走 `asyncio.set_event_loop_policy`。

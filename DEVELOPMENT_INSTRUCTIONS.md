# 企业数据智能助手 — 运维与开发文档

> **项目周期：** 2025.11 - 2026.04  
> **当前状态：** 已完成开发，后端 8002 端口 + 前端 SPA 运行中

---

## 一、快速开始

### 1.1 环境要求

- Python 3.10+
- Node.js 18+（仅前端开发需要）
- MySQL 8.0+（需提前创建数据库 `data_processing_platform`）

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
MYSQL_PASSWORD=你的MySQL密码
```

### 1.4 初始化数据库

```bash
python scripts/init_db.py
```

### 1.5 启动

**Windows — 双击 `start.bat`** 或终端执行：

```powershell
.\start.ps1
```

**手动启动（跨平台）：**

```bash
# 后端（内置前端 SPA，单端口即可）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
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
  → 标准化入库（MySQL 统一 schema）
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
| ORM | SQLAlchemy 2.0 + PyMySQL | MySQL 连接与批量操作 |
| 数据处理 | Pandas + NumPy | 数据清洗和转换 |
| LLM | 通义千问 qwen-turbo（DashScope） | 表头对齐、异常判定、PDF 提取、Agent、RAG 答案生成 |
| Agent 框架 | LangChain 1.x + LangGraph | ReAct Agent + SqliteSaver Checkpointer |
| 向量检索 | ChromaDB + sentence-transformers (BGE) | RAG 文档向量化与检索 |
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
│  └──────────────────┘ │  (BGE + ChromaDB + BM25 混合检索)      │ │
│  ┌──────────────────┐ └────────────────────────────────────────┘ │
│  │   Query Agent    │                                            │
│  │ LangChain ReAct  │                                            │
│  │ + SqliteSaver    │                                            │
│  └──────────────────┘                                            │
├──────────────────────────────────────────────────────────────────┤
│  Database: MySQL (SQLAlchemy ORM)                                │
│  Tables: data_sources / raw_records / cleaned_records /          │
│          pipeline_logs / schema_mappings / chat_history /        │
│          query_trace                                             │
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
│   │   └── schema_mapping.py      # 表头映射配置表
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
│   │   ├── memory_store.py        # 业务口径长期记忆（ChromaDB + 去重/遗忘）
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
│   │   │   └── vector_store.py    # ChromaDB 索引管理
│   │   └── skills/
│   │       ├── __init__.py
│   │       ├── base.py            # Skill 抽象（元数据 + handler）
│   │       ├── registry.py        # Skill 注册表（intent → Skill）
│   │       ├── instructions.py    # 工具操作手册（按 intent 注入）
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
  │         p_top < 0.75 时不走单引擎，升级为 hybrid 安全网
  ├── data_query → LangChain ReAct Agent → SQL 查询 → 结构化数据回答
  ├── doc_query  → RAG Pipeline → 文档检索 → 引用溯源回答
  └── hybrid     → 两边并行 → LLM 合成统一答案（SSE 流式返回）
```

- **Intent Router** (`intent_router.py`)：不读模型自报的 confidence，直接读首 token 的 logprobs 分布。
  - 标签写成单 token 的 `数`/`文`/`混`（`tokenizer.encode("data_query")` 是 2 个 token，多 token 标签只能拿到首 token 处的边缘概率，且不报错）
  - `max_tokens=1` + `temperature=1` + `top_logprobs=5`（写死常量，合法区间 `[0,5]`，越界 HTTP 400）
  - 派生量：`p_top`（阈值主指标）、`label_mass`（prompt 约束健康度）、`margin`；**绝不重新归一化**，否则会抹掉 `label_mass` 这个信号
  - 阈值 0.75 由成本比推出：`(1 - p_top) × C_e > C_h` → `p_top < 1 - C_h / C_e`，取 `C_h = 1`、`C_e = 4`
  - 降级链 L0–L3：启动时校验标签单 token（fail fast）→ 重试（预算由端到端 P99 反推）→ 规则降级（匹配**用户问题**，confidence 被常量卡在阈值下）→ 兜底与熔断
  - 每次路由落一条结构化日志（`path` / `degrade` / `top5` 原始分布），供离线重调阈值
- **Query Agent** (`query_agent.py`)：LangChain 1.x `create_agent` + `@tool` 装饰器定义工具 + SqliteSaver Checkpointer 持久化对话
- **路由入口** (`routers/query.py`)：三路并发 + SSE 流式响应；意图路由用 `asyncio.to_thread` 包一层，避免同步 LLM 调用阻塞事件循环

### 5.2 RAG 管线（services/rag/）

6 个文件组成完整管线：

| 文件 | 职责 |
|---|---|
| `document_loader.py` | 加载 .txt/.md/.pdf 等多格式文档 |
| `chunker.py` | 文档切分为语义块 |
| `embedder.py` | BGE 模型向量化 |
| `vector_store.py` | ChromaDB 索引管理（建索引、统计、增量更新） |
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

```sql
CREATE TABLE data_sources (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL COMMENT '数据源名称',
    source_type ENUM('excel','csv','mysql','pdf') NOT NULL,
    file_path VARCHAR(500) COMMENT '文件路径（excel/csv/pdf）',
    db_host VARCHAR(200) COMMENT 'MySQL 主机',
    db_port INT DEFAULT 3306,
    db_name VARCHAR(100),
    db_user VARCHAR(100),
    db_password VARCHAR(200),
    db_query VARCHAR(1000) COMMENT 'SQL 查询',
    sheet_name VARCHAR(100) COMMENT 'Excel sheet 名',
    delimiter VARCHAR(10) DEFAULT ',',
    encoding VARCHAR(20) DEFAULT 'utf-8',
    skip_rows INT DEFAULT 0,
    status ENUM('active','inactive','error') DEFAULT 'active',
    last_collect_at DATETIME,
    last_collect_count INT DEFAULT 0,
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB CHARSET=utf8mb4;
```

### 6.2 原始采集记录表 `raw_records`

```sql
CREATE TABLE raw_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_id INT NOT NULL,
    batch_id VARCHAR(50) NOT NULL,
    raw_data JSON NOT NULL COMMENT '原始数据（保留所有字段，不做清洗）',
    source_row_index INT COMMENT '在原文件中的行号',
    status ENUM('raw','aligned','cleaned','error') DEFAULT 'raw',
    error_info TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES data_sources(id) ON DELETE CASCADE,
    INDEX idx_source_id (source_id),
    INDEX idx_batch_id (batch_id),
    INDEX idx_status (status)
) ENGINE=InnoDB CHARSET=utf8mb4;
```

### 6.3 清洗后统一记录表 `cleaned_records`

```sql
CREATE TABLE cleaned_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    batch_id VARCHAR(50) NOT NULL,
    source_id INT NOT NULL,
    employee_name VARCHAR(100),
    employee_id VARCHAR(50),
    department VARCHAR(100),
    data_type VARCHAR(50) COMMENT 'attendance/sales/customer/operation',
    record_date DATE,
    business_data JSON NOT NULL COMMENT '标准化后的业务字段',
    quality_score FLOAT DEFAULT 1.0,
    is_anomaly BOOLEAN DEFAULT FALSE,
    anomaly_reason TEXT,
    anomaly_status VARCHAR(30) DEFAULT 'normal' COMMENT '异常判定状态: normal/pending_review/confirmed',
    pending_check_fields JSON COMMENT '待审核字段（LLM降级时保留的IQR候选信息）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES data_sources(id) ON DELETE CASCADE,
    INDEX idx_batch_id (batch_id),
    INDEX idx_data_type (data_type),
    INDEX idx_record_date (record_date)
) ENGINE=InnoDB CHARSET=utf8mb4;
```

### 6.4 表头映射配置表 `schema_mappings`

```sql
CREATE TABLE schema_mappings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_id INT NOT NULL,
    mapping_data JSON NOT NULL COMMENT '原始表头→标准字段映射',
    is_llm_generated BOOLEAN DEFAULT TRUE,
    confidence FLOAT,
    manual_reviewed BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES data_sources(id) ON DELETE CASCADE
) ENGINE=InnoDB CHARSET=utf8mb4;
```

### 6.5 清洗流水日志表 `pipeline_logs`

```sql
CREATE TABLE pipeline_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    batch_id VARCHAR(50) NOT NULL,
    step_name VARCHAR(50) NOT NULL COMMENT 'dedup/fill_missing/anomaly_detect',
    input_count INT DEFAULT 0,
    output_count INT DEFAULT 0,
    affected_count INT DEFAULT 0,
    details JSON COMMENT '清洗规则及 LLM 判断详情',
    duration_seconds FLOAT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_batch_id (batch_id)
) ENGINE=InnoDB CHARSET=utf8mb4;
```

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
| `MYSQL_PASSWORD` | 是 | — | MySQL 密码 |
| `MYSQL_HOST` | 否 | `localhost` | MySQL 主机 |
| `MYSQL_PORT` | 否 | `3306` | MySQL 端口 |
| `MYSQL_USER` | 否 | `root` | MySQL 用户名 |
| `MYSQL_DB` | 否 | `data_processing_platform` | 数据库名 |
| `LLM_MODEL` | 否 | `qwen-turbo` | LLM 模型名 |

实际代码见 `app/config.py` 中的 `Settings` 类。

---

## 九、关键注意事项

1. **LLM 调用成本控制**：表头对齐每个数据源只调用一次并缓存；异常判异只送 IQR 筛选后的候选，候选按单批容量分批全量送审、不做数量截断（候选只占总数据量的千分之一量级，注意力容量才是分批的理由，成本不是）。
2. **不允许完全依赖 LLM**：LLM 判异结果是辅助性的，用户可人工覆盖。最终决定权在业务人员。LLM 调用失败时，IQR 候选自动降级为 `pending_review` 状态保留，不做丢弃。
3. **原始数据不可覆盖**：`raw_records` 表保留原始 JSON，清洗和标准化都在派生表上进行。
4. **PDF 提取的局限性**：纯扫描件 PDF 需要额外的 OCR（PaddleOCR/Tesseract），当前架构未集成。首期只支持文本型 PDF。
5. **MySQL 连接安全**：生产环境建议使用 `cryptography` 库的 Fernet 对称加密存储数据源密码。
6. **线程安全**：数据库驱动使用 PyMySQL（非 mysql-connector），解决并发请求时的线程安全问题。
7. **前端部署**：前端编译为静态文件后由 FastAPI 直接 serve，单端口 8002 即可访问完整应用（无需 Nginx 反代或独立前端服务器）。
8. **Agent Checkpointer**：使用 LangGraph `SqliteSaver` 持久化（服务重启不丢对话历史），不可用时回退 `MemorySaver`。

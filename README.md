# 自动化客户数据处理平台

多源异构数据采集、清洗、标准化与智能分析平台。支持 Excel / CSV / MySQL / PDF 四种数据源自动接入，通过 LLM 驱动的清洗 Pipeline 和 LangChain Agent 实现自然语言交互式数据查询。

## ✨ 核心功能

- **多源数据采集** — 统一的 Connector 抽象层，一键接入 Excel、CSV、MySQL、PDF 四种数据源
- **PDF 智能提取** — 通义千问大模型将非结构化 PDF 报表自动转换为结构化表格
- **异构表头对齐** — LLM 语义匹配，自动将不同部门的表头命名映射到统一标准字段
- **数据清洗 Pipeline** — 去重 → 缺失值填充 → IQR 统计初筛 + LLM 业务异常判定（LLM 失败自动降级为待人工审核），三步流水线
- **自然语言智能查询** — 用中文提问（"销售部 11 月异常率多少？"），Agent 自动理解意图、执行查询、解释结果
- **RAG 制度文档问答** — BGE 向量化 + BM25 混合检索 + 引用溯源，让 AI 基于企业内部文档回答制度问题
- **LLM 调用容错** — 指数退避重试 + 可重试/不可重试分类，异常判定 LLM 失败自动降级为待人工审核
- **可观测性 Trace** — 每次查询落 `query_trace` 结构化记录（意图/工具/耗时），支撑 badcase 归因
- **可视化分析报表** — 数据质量报告、趋势分析、异常审核，支持 Excel 导出

## 🏗️ 技术栈

| 层 | 技术 |
|---|---|
| **后端框架** | FastAPI（异步高性能，自动生成 Swagger 文档） |
| **ORM** | SQLAlchemy 2.0 + PyMySQL |
| **数据处理** | Pandas + NumPy |
| **LLM** | 通义千问 qwen-turbo（DashScope） |
| **Agent 框架** | LangChain 1.x + LangGraph（ReAct Agent + SqliteSaver Checkpointer） |
| **向量检索** | ChromaDB + BGE（sentence-transformers）+ BM25 混合检索 |
| **PDF 提取** | pdfplumber + LLM 结构化 |
| **前端** | Vue 3 + Vite + TypeScript |
| **UI** | Element Plus + ECharts + Tailwind CSS v4 |
| **数据库** | MySQL 8.0 |

## 🚀 快速开始

### 环境要求

- Python 3.10+
- MySQL 8.0+
- Node.js 18+（仅前端开发）

### 1. 克隆项目

```bash
git clone https://github.com/qliu3344-art/-.git
cd 自动化客户数据处理平台
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

创建 `.env` 文件：

```
DASHSCOPE_API_KEY=你的通义千问API密钥
MYSQL_PASSWORD=你的MySQL密码
```

### 4. 初始化数据库

确保 MySQL 已运行，然后：

```bash
python scripts/init_db.py
```

### 5. 启动

**Windows — 双击 `start.bat`** 或在终端运行：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

启动后访问：

| 地址 | 说明 |
|---|---|
| http://localhost:8002 | 前端页面 |
| http://localhost:8002/docs | Swagger API 文档 |
| http://localhost:8002/health | 健康检查 |

### 前端开发模式

```bash
cd frontend
npm install
npm run dev       # Vite 开发服务器，支持 HMR
npm run build     # 构建到 app/static/，由后端 serve
```

## 📖 架构概览

```
用户提问（自然语言）
  → Intent Router（LLM 分类：查数据 / 查文档 / 混合）
    ├─ data_query → LangChain ReAct Agent → SQL 查询 → 数据回答
    ├─ doc_query  → RAG Pipeline → 混合检索 → 引用溯源回答
    └─ hybrid     → 双引擎并行 → LLM 合成统一答案（SSE 流式返回）
```

```
数据采集 → 表头对齐 → 三步清洗 → 标准化入库 → 分析报表
    │          │          │
    │     LLM 语义映射    IQR + LLM 判异
    │
  Connector 策略模式（Excel / CSV / MySQL / PDF+LLM）
```

## 📁 项目结构

```
├── app/
│   ├── main.py              # FastAPI 入口，注册 6 个 Router
│   ├── config.py            # 配置管理（.env → Settings）
│   ├── database.py          # SQLAlchemy 引擎与 Session
│   ├── models/              # 8 张数据表 ORM 模型
│   ├── schemas/             # Pydantic 请求/响应模型
│   ├── routers/             # 6 组 REST API
│   │   ├── datasource.py    # 数据源 CRUD
│   │   ├── collect.py       # 采集触发
│   │   ├── clean.py         # 清洗管理
│   │   ├── analysis.py      # 分析报表
│   │   ├── query.py         # 自然语言查询（SSE 流式）
│   │   └── rag.py           # RAG 制度问答
│   └── services/
│       ├── connectors/      # 4 种数据源连接器（策略模式）
│       ├── aligner.py       # LLM 异构表头对齐
│       ├── cleaner.py       # 三步清洗 Pipeline
│       ├── collector.py     # 采集编排
│       ├── analyzer.py      # 多维度分析
│       ├── intent_router.py # LLM 意图分类
│       ├── query_agent.py   # LangChain ReAct Agent
│       └── rag/             # RAG 管线（加载/切分/向量化/检索/生成）
├── frontend/                # Vue 3 SPA
├── scripts/                 # 初始化、模拟数据、测试脚本
├── eval.py                  # 四维度 Agent 评估
├── eval_dataset.json        # 评估数据集
├── data/
│   ├── documents/           # RAG 制度文档
│   └── chroma_db/           # 向量数据库
├── start.bat                # Windows 一键启动
└── requirements.txt
```

## 🔌 API 一览

### 数据源管理 `POST/GET /api/v1/datasources`

注册、编辑、删除数据源，上传数据文件，测试连接。

### 数据采集 `POST /api/v1/collect`

触发单源或批量采集，查询采集历史和批次详情。

### 数据清洗 `POST /api/v1/clean`

对采集批次执行三步清洗，查询清洗日志和异常记录，支持人工修正。

### 分析报表 `GET /api/v1/analysis`

仪表盘综合数据、汇总统计、趋势分析、数据质量报告、Excel 导出。

### 智能查询 `POST /api/v1/query`

```json
{
  "question": "销售部上个月订单金额最高的前 5 名员工是谁？",
  "thread_id": "session_001"
}
```

SSE 流式返回，支持多轮对话。

### RAG 问答 `POST /api/v1/rag/ask`

```json
{
  "question": "加班费怎么算？"
}
```

返回答案 + 引用原文来源。

## 🧪 评估体系

```bash
python eval.py                      # 全量四维度评估
python eval.py --category data_query # 只测数据查询类
python eval.py --skip-agent          # 只测意图分类 + 答案质量（无需数据库）
```

| 维度 | 权重 | 说明 |
|---|---|---|
| 意图分类准确率 | 25% | LLM 是否正确判断 data/doc/hybrid |
| 工具选择准确率 | 25% | Agent 是否调用了预期工具 |
| RAG 检索命中率 | 15% | 关键词命中（基线）+ LLM 语义相关性（主指标） |
| 答案质量评分 | 35% | LLM-as-Judge 1-5 分评分 |

## ⚠️ 注意事项

- **LLM 调用成本** — 表头对齐每数据源只调用一次并缓存；异常判异只送 IQR 筛选后的候选（IQR 已初筛，不再二次截断）
- **不依赖 LLM** — 异常判定结果为辅助参考，业务人员可人工覆盖
- **原始数据保护** — `raw_records` 保留原始 JSON，清洗后的数据写入独立表
- **PDF 限制** — 当前仅支持文本型 PDF，扫描件需额外 OCR
- **Agent 持久化** — 默认 SqliteSaver 持久化（服务重启不丢对话历史），SqliteSaver 不可用时回退 MemorySaver

## 📄 License

MIT

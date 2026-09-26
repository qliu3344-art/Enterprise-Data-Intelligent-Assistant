# 企业数据智能助手

多源异构数据采集、清洗、标准化与智能分析平台。支持 Excel / CSV / MySQL / PDF 四种数据源自动接入，通过 LLM 驱动的清洗 Pipeline 和 LangChain Agent 实现自然语言交互式数据查询。

## ✨ 核心功能

- **多源数据采集** — 统一的 Connector 抽象层，一键接入 Excel、CSV、MySQL、PDF 四种数据源
- **PDF 智能提取** — 通义千问大模型将非结构化 PDF 报表自动转换为结构化表格
- **异构表头对齐** — LLM 语义匹配 + 置信度评分，低置信度标红人工确认，未确认的映射清洗时拦截
- **数据清洗 Pipeline** — 去重 → 缺失值填充 → IQR 统计初筛 + LLM 业务异常判定（LLM 失败自动降级为待人工审核），三步流水线
- **候选不截断** — 偏离度排序只决定「先看谁」，不决定「看谁」；候选按单批容量分批全量送审，覆盖百分之百，一条不丢
- **logprobs 意图路由** — 用中文提问（"销售部 11 月异常率多少？"），不靠模型自报置信度，直接读首 token 的真实概率分布（`p_top` / `label_mass` / `margin`）；阈值 0.75 由成本比 `C_h:C_e = 1:4` 推导，低于阈值升级走双引擎安全网
- **RAG 制度文档问答** — BGE 向量化 + BM25 混合检索 + 引用溯源，让 AI 基于企业内部文档回答制度问题
- **多轮对话上下文压缩** — 滑动窗口保留最近原文 + LLM 摘要压缩旧历史 + 业务口径记忆按需召回，短中长三级防止上下文溢出
- **四层降级链** — L0 参数防线（标签单 token 启动校验，fail fast）→ L1 重试（预算由端到端 P99 反推）→ L2 规则降级（匹配用户问题而非模型输出，confidence 上界卡死在阈值之下）→ L3 兜底与熔断
- **可观测性 Trace** — 每次查询落 `query_trace`：全链路 `trace_id`、工具名 + **实际参数**、分段耗时（意图/检索/Agent/融合）、意图路由的完整分布，支撑 badcase 归因与离线重调阈值
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
git clone https://github.com/qliu3344-art/Enterprise-Data-Intelligent-Assistant.git
cd 企业数据智能助手
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
npm run build     # 构建到 frontend/dist，由后端 serve（SPA fallback）
```

## 📖 架构概览

```
用户提问（自然语言）
  → Intent Router（读 logprobs 真实分布 → p_top / label_mass / margin）
    │    p_top < 0.75 → 升级走 hybrid 安全网（宁可多查不漏）
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

## 🎯 意图路由：为什么不让模型自报置信度

让模型输出一个 `confidence` 字段，本质是**模型的第二次生成**——一次新的、可能被 prompt 引导的表述。logprobs 则是**第一次生成时就已存在的内部状态**，无法伪装。

一句话：**概率是模型自己的，不是它嘴上说的。**

| 设计点 | 做法 | 为什么 |
|---|---|---|
| 标签单 token | 标签写成 `数`/`文`/`混` | `tokenizer.encode("data_query")` 是 2 个 token，多 token 标签只能拿到首 token 处的**边缘概率**，且不报错——静默错误 |
| `max_tokens=1` | 只取第一个位置的分布 | 消掉「生成后面内容反过来影响首 token」的可能 |
| `temperature=1` | 保持模型出厂分布 | 温度作用在 softmax 之前，压低温度会把分布人为压陡、`p_top` 虚高 |
| `top_logprobs=5` | 写死常量 | 合法区间 `[0, 5]`，越界直接 HTTP 400 |
| 三个派生量 | `p_top` / `label_mass` / `margin` | 前两个能区分「模型在纠结」和「prompt 没约束住」，这是自评置信度给不了的诊断维度 |
| **绝不重新归一化** | 直接读原始概率 | 归一化看着更像置信度，但正好抹掉 `label_mass` 这个信号 |

**阈值由成本推导，不拍脑袋。** 误判代价不对称：高置信度走错路的代价是 `C_e`，低置信度触发安全网只是多跑一路 hybrid（`C_h`）。只有期望错误代价超过安全网成本时才值得升级：

```
(1 - p_top) × C_e > C_h   →   p_top < 1 - C_h / C_e
取 C_h = 1、C_e = 4  →  阈值 0.75
```

降级分四层：**L0** 参数防线（标签单 token 启动校验，失败即拒绝启动）→ **L1** 重试（预算按端到端 P99 反推，只重试超时/限流/5xx，带随机抖动）→ **L2** 规则降级（匹配**用户问题**的关键词表，`confidence` 被常量卡在阈值之下，强制走安全网）→ **L3** 兜底与熔断（砍掉需要 LLM 的那一步，保留 SQL 执行和向量检索）。

> 低置信度**不是降级，是路由**：手里还有模型给出的真实分布，只是决定不信它。真正的降级发生在**连分布都没拿到**的时候。

## 📁 项目结构

```
├── app/
│   ├── main.py              # FastAPI 入口，注册 7 个 Router
│   ├── config.py            # 配置管理（.env → Settings）
│   ├── database.py          # SQLAlchemy 引擎与 Session
│   ├── mcp_server.py        # MCP Server（查询能力对外标准化出口）
│   ├── models/              # 8 张数据表 ORM 模型
│   ├── schemas/             # Pydantic 请求/响应模型
│   ├── routers/             # 7 组 REST API
│   │   ├── datasource.py    # 数据源 CRUD
│   │   ├── collect.py       # 采集触发
│   │   ├── clean.py         # 清洗管理
│   │   ├── analysis.py      # 分析报表
│   │   ├── query.py         # 自然语言查询（SSE 流式）
│   │   ├── rag.py           # RAG 制度问答
│   │   └── feedback.py      # 用户反馈
│   └── services/
│       ├── connectors/      # 4 种数据源连接器（策略模式）
│       ├── skills/          # Skill 注册表（意图 → 能力，按需加载）
│       ├── aligner.py       # LLM 异构表头对齐
│       ├── cleaner.py       # 三步清洗 Pipeline
│       ├── collector.py     # 采集编排
│       ├── analyzer.py      # 多维度分析
│       ├── intent_router.py # logprobs 意图路由（分布 → p_top/label_mass/margin）
│       ├── query_agent.py   # LangChain ReAct Agent
│       ├── data_query_ops.py  # 数据查询原子操作（Agent 与 MCP 共用）
│       ├── context_manager.py # 多轮对话上下文压缩（滑动窗口+摘要）
│       ├── memory_store.py    # 业务口径记忆（改口更新 + 主动遗忘 + 上限）
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
  "session_id": "session_001"
}
```

返回 JSON 结果；传入相同 `session_id` 可保持多轮对话上下文（留空则每次独立）。

流式版本 `POST /api/v1/query/stream` 以 SSE 实时推送八种事件，顺序固定：

```
intent → agent_start → tool_call ⇄ tool_result → answer → done
                                        ↑ RAG 侧多一个 rag_ready
```

`done` 必须无条件发（写在 `finally` 分支）——正常结束、抛异常、甚至客户端中途断开，前端都必须收到一个终点，否则 loading 永远转。`error` 之后也要补 `done`：已经推出去的 `tool_result` 撤不回来，所以错误处理不是「终止」而是「收尾」。

### RAG 问答 `POST /api/v1/rag/ask`

```json
{
  "question": "加班费怎么算？"
}
```

返回答案 + 引用原文来源。

### 用户反馈 `POST/GET /api/v1/feedback`

对助手回答点赞/点踩（rating = 1 / -1），点踩可附纠错文本。后端 best-effort 关联最近一次同问题的 `query_trace`，便于 badcase 归因。

点踩反馈可经 `scripts/sync_feedback_to_dataset.py` 半自动回灌评估数据集（ground truth 由人工确认）。

## 🧪 评估体系

```bash
python eval.py                       # 全量四维度评估
python eval.py --category data_query # 只测数据查询类
python eval.py --skip-agent          # 只测意图分类 + RAG + LLM Judge（无需数据库）
python eval.py --skip-rag-semantic   # 跳过语义 RAG，只保留关键词命中率（省钱）
```

| 维度 | 权重 | 说明 |
|---|---|---|
| 意图分类准确率 | 25% | LLM 是否正确判断 data/doc/hybrid |
| 工具选择准确率 | 25% | Agent 是否调用了预期工具 |
| RAG 检索命中率 | 15% | 关键词命中（基线）+ LLM 语义相关性（主指标） |
| 答案质量评分 | 35% | LLM-as-Judge 1-5 分评分 |

## ⚠️ 注意事项

- **LLM 调用成本** — 表头对齐每数据源只调用一次并缓存；异常判异只送 IQR 筛选后的候选，并按单批容量**分批全量**送审，不做数量截断（成本不是截断的理由：一次清洗省掉的是人工几小时的核对）
- **意图路由的启动校验** — 服务启动时会用 tokenizer 逐个校验标签长度必须为 1，不满足直接启动失败。多 token 标签不会报错，只会安静地返回一个偏高的错误概率，属于最危险的静默错误
- **不依赖 LLM** — 异常判定结果为辅助参考，业务人员可人工覆盖
- **原始数据保护** — `raw_records` 保留原始 JSON，清洗后的数据写入独立表
- **PDF 限制** — 当前仅支持文本型 PDF，扫描件需额外 OCR
- **Agent 持久化** — 默认 SqliteSaver 持久化（服务重启不丢对话历史），SqliteSaver 不可用时回退 MemorySaver

## 📄 License

MIT

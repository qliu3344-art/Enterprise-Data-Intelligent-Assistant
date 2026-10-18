# 企业数据智能助手

多源异构数据采集、清洗、标准化与智能分析平台。支持 Excel / CSV / MySQL / PDF 四种数据源自动接入，通过 LLM 驱动的清洗 Pipeline 和 LangChain Agent 实现自然语言交互式数据查询。

## ✨ 核心功能

- **多源数据采集** — 统一的 Connector 抽象层，一键接入 Excel、CSV、MySQL、PDF 四种数据源
- **PDF 智能提取** — 通义千问大模型将非结构化 PDF 报表自动转换为结构化表格
- **异构表头对齐** — LLM 语义匹配 + 置信度评分，低置信度标红人工确认，未确认的映射清洗时拦截
- **数据清洗 Pipeline** — 去重 → 缺失值填充 → IQR 统计初筛 + LLM 业务异常判定（LLM 失败自动降级为待人工审核），三步流水线
- **候选不截断** — 偏离度排序只决定「先看谁」，不决定「看谁」；候选按单批容量分批全量送审，覆盖百分之百，一条不丢
- **logprobs 意图路由** — 用中文提问（"销售部 11 月异常率多少？"），不靠模型自报置信度，直接读首 token 的真实概率分布（`p_top` / `label_mass` / `margin`）；阈值 0.99 由 692 条实测扫出（扫「每消掉 1 条错误答案要付几次无谓升级」，取便宜区末端），低于阈值升级走双引擎安全网
- **RAG 制度文档问答** — BGE 向量化 + BM25 混合检索 + 引用溯源，让 AI 基于企业内部文档回答制度问题
- **多轮对话上下文压缩** — 滑动窗口保留最近原文 + LLM 摘要压缩旧历史 + 业务口径记忆按需召回，短中长三级防止上下文溢出
- **业务口径治理** — 公司级正式口径以制度文档为唯一权威定义，部门级按部门隔离召回，会话级临时限定不进长期记忆；用户新交代的口径先进候选池，本会话立即生效、推广给其他人要过审核（`scripts/review_conventions.py`）
- **四层降级链** — L0 参数防线（标签单 token 启动校验，fail fast）→ L1 重试（预算由端到端 P99 反推）→ L2 规则降级（匹配用户问题而非模型输出，confidence 上界卡死在阈值之下）→ L3 兜底与熔断
- **可观测性 Trace** — 每次查询落 `query_trace`：全链路 `trace_id`、工具名 + **实际参数**、分段耗时（意图/检索/Agent/融合）、意图路由的完整分布，支撑 badcase 归因与离线重调阈值
- **可视化分析报表** — 数据质量报告、趋势分析、异常审核，支持 Excel 导出

## 🏗️ 技术栈

| 层 | 技术 |
|---|---|
| **后端框架** | FastAPI（异步高性能，自动生成 Swagger 文档） |
| **ORM / 驱动** | SQLAlchemy 2.0 + psycopg 3 |
| **数据处理** | Pandas + NumPy |
| **LLM** | 通义千问 qwen-flash（DashScope） |
| **Agent 框架** | LangChain 1.x + LangGraph（ReAct Agent + Postgres Checkpointer） |
| **向量检索** | pgvector（与业务数据**同库**）+ BGE（sentence-transformers）+ BM25 混合检索 |
| **PDF 提取** | pdfplumber + LLM 结构化 |
| **前端** | Vue 3 + Vite + TypeScript |
| **UI** | Element Plus + ECharts + Tailwind CSS v4 |
| **数据库** | PostgreSQL 17 + pgvector 0.8 |

> 向量**不另起一套存储**，和业务数据在同一个 PostgreSQL 里（pgvector 扩展）。两条理由：一是元数据过滤是原生的——`doc_title` / `chapter` / 生效日期就是普通列，`WHERE` 过滤、跟业务表 join 都是原生 SQL，不用翻译成向量库自己的过滤语法；二是**同库同事务**——制度改版时「删旧 chunk、插新 chunk、更新向量」在同一个 `BEGIN/COMMIT` 之间完成，跨库做不到。详见下文「存储选型」一节。
>
> ⚠️ 数据源里的 **MySQL 是另一回事**：那是 `MySQLConnector` 去读「业务部门自己维护的业务库」，属于四类接入源之一，与应用自身的存储无关。

## 🚀 快速开始

### 环境要求

- Python 3.10+
- PostgreSQL 16+，并安装 **pgvector 扩展**（本项目在 PG 17 + pgvector 0.8 上验证）
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
PG_PASSWORD=你的PostgreSQL密码
```

主机 / 端口 / 用户名 / 库名的默认值在 `app/config.py` 的 `Settings` 里（`localhost:5432` / `postgres` / `data_processing_platform`）。

### 4. 准备数据库与初始化

先建库（`CREATE DATABASE` 不能在事务里执行，所以由 DBA 侧预建，不放进应用脚本）：

```bash
psql -U postgres -c "CREATE DATABASE data_processing_platform"
psql -U postgres -d data_processing_platform -c "CREATE EXTENSION IF NOT EXISTS vector"
```

再建表：

```bash
python scripts/init_db.py
```

> `init_db.py` 自己也会 `CREATE EXTENSION IF NOT EXISTS vector`（建模前必须先有 `vector` 类型，否则 `Vector(512)` 的 DDL 会报 `type "vector" does not exist`）。上面这一步是给最小权限账号准备的。

### 5. 启动

**Windows — 双击 `start.bat`** 或在终端运行：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --loop app.main:selector_loop_factory
```

> `--loop` 不能省：会话状态走 psycopg 的异步模式，而 uvicorn 在 Windows 上默认选的
> `ProactorEventLoop` 不支持它。省掉的后果是**启动直接失败**（启动日志里会写明这一条），
> 不是静默降级——会话检查点连不上就不该假装健康。

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
    │    p_top < 0.99 → 升级走 hybrid 安全网（宁可多查不漏）
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

**阈值有两条约束：一条结构性的下界，一条实测的取值。**

**下界是结构性的**——必须高于 `RULE_CONFIDENCE_CAP`（0.70）。规则路径拿不出校准过的概率，置信度被刻意压在 0.70 以下；阈值高于它，每条走规则的请求才会被推去 hybrid。低于它，规则就冒充高置信模型直接放行，安全网破洞。

**取值是扫出来的**——把「漏答 + 单↔单错答」算作错误、「本来答对却被升级」算作代价，在 692 条上逐点扫兑换率（每消掉 1 条错误答案要付几次无谓升级）：

```
0.75 → 0.99     4 ~ 7.5
0.99 → 0.999    17
0.999 → 1.0     30+

取 0.99 —— 便宜区的末端
```

不用成本公式 `(1 - C_h/C_e)` 反推：它假定 `p_top = 1 - P(判错)`，也就是模型是校准的。实测不是——错误质量集中在 `p_top > 0.9998`，模型不是「不确定地错」，是「很自信地错」，低置信度区间里几乎没有错误，按概率去升级它们等于白升。

降级分四层：**L0** 参数防线（标签单 token 启动校验，失败即拒绝启动）→ **L1** 重试（预算按端到端 P99 反推，只重试超时/限流/5xx，带随机抖动）→ **L2** 规则降级（匹配**用户问题**的关键词表，`confidence` 被常量卡在阈值之下，强制走安全网）→ **L3** 兜底与熔断（砍掉需要 LLM 的那一步，保留 SQL 执行和向量检索）。

> 低置信度**不是降级，是路由**：手里还有模型给出的真实分布，只是决定不信它。真正的降级发生在**连分布都没拿到**的时候。

## 🧭 模型选型：先划掉不合格的，再比价格

项目周期是 **2025 年 5 月 — 2026 年 1 月**，所以判据是**「那个窗口内能不能用」**，不是「今天还在不在售」。以下结论全部锚在这个时间窗上。

第一道筛子不是「谁更强」，是**「谁支持 logprobs」**——整套意图路由建在首 token 分布上，不支持就直接出局，多便宜都没用。而这条筛子在当时把选择面收得很窄：

| 候选 | 窗口内可用 | 结论 |
|---|---|---|
| DeepSeek V3 / R1 | 是 | **出局** — 官方 API 未提供 logprobs：推理模型文档列为不支持，**且设置了不报错、静默忽略**；V3.2（2025-09 发布，在窗口内）实测只返回 `0 / -9999`、候选列表恒空。「不报错的错」正是本项目最忌讳的失败模式 |
| 豆包 Seed 1.6 | 是 | **出局** — 火山方舟对 `logprobs` / `top_logprobs` 的协议支持是 **2026-09-09** 的兼容性升级才引入的，窗口内不具备该能力 |
| Kimi / GLM | 是 | **存疑** — 查不到窗口期内的官方支持依据（只有第三方网关声称支持），不作为选型依据 |
| **qwen-flash** | 是（2025-07-28 发布） | **选用** — DashScope 全程提供 logprobs，适配层本就是针对它的返回结构写的；同 SDK、零改造 |

**结论：在那个窗口里，只有通义千问这一条线能稳定拿到首 token 分布。**这不是偏好，是筛出来的。

**选 qwen-flash 而不选更大的 qwen-max**，是因为路由模型考的不是「回答得多好」，而是**「分布给得多完整」**：实测 qwen-max 只返回 1 个候选（`top_logprobs=5` 被忽略），`margin` 与 `label_mass` 会一起退化成常量 `1.0`——**不报错，直接失去诊断能力**。模型越大越好这个直觉，在路由这一层是错的。

成本上，本项目的调用以长 prompt 为主（Agent 历史、清洗批次候选、RAG 文档块），**输入单价才是主成本杠杆**：qwen-flash 输入 0.15 元/百万 token，是 qwen-turbo（0.3~0.367）的一半或更低；输出 1.5，与 turbo 同期快照的 1.468 基本持平；缓存命中输入仅 0.03。它同时也是 qwen-turbo 的官方继任者（2025-07-28 发布）。

**迁移时间线**：项目 2025 年 5 月启动时用的是当时现役的 qwen-turbo；qwen-flash 于 2025 年 7 月 28 日发布后迁移过去，输入成本随之减半。换模型不是改个配置字符串就完事——路由依赖 tokenizer 与返回体的结构，迁移前先实测了候选模型的 logprobs 与 function calling 行为，再靠启动时的标签单 token 校验兜住分词差异。

## 🗄️ 存储选型：为什么向量不另起一套存储

**判据不是性能，是故障面。** 这个库的向量总量只有几兆，检索是毫秒级，专用向量库那一套能力在这里用不上；而在能力已经富余的前提下，每多一个组件就多一套监控、备份、高可用和连接池要养。**选型不是选最强的那一个，是选刚好够用、故障面最小的那一个。**

这条判据不是在 1,500 条条款这个尺度上才成立的——它在量大三四个数量级的系统上被反复验证过：

| 案例 | 规模 | 选型理由（他们公开写的） |
|---|---|---|
| 亚马逊 CORTO（Aurora PostgreSQL） | 46 TB / 25 亿文档 / 76 亿向量 | PostgreSQL 能同时承担事务、连接过滤、向量召回三件事 |
| 亚马逊 Ring（RDS PostgreSQL） | 1000–2000 亿向量，日增约 20 亿 | 按用户分区，**故意不建向量索引**，暴力扫描保 100% 召回 |
| PolarDB / RDS PostgreSQL | 亿级向量毫秒级 | 量化把单条向量开销压到 1/4 ~ 1/64 |
| 百原多租户 RAG 平台 | 多租户 | 「选它而不是独立向量库，关键是**减少单点故障和运维简化**，不是性能」 |

**同库真正买到的是一条事务边界。** 制度改版时，「删旧 chunk、插新 chunk、更新向量」必须在同一个事务里提交——这是跨库给不了的东西，也是选它最硬的一条理由。跨库的失败形态是静默的：向量库那一步超时或进程被重启，业务库里是新规、向量库里还是旧向量，接口 200、日志干净、监控不响、答案读起来完全通顺，模型一本正经地引用一条已作废的条款。

**为什么不上 HNSW 索引。** 这个体量下顺序扫描本身就是毫秒级、而且召回是 100%，近似索引的损失换不到东西；融合吃的是**排名**，掉一个真实邻居就会改 RRF 结果，而这类损失是静默的。且每次重建都要维护图，成本加在写路径上。规模涨到十万级、或 p95 超过 50 ms 再加（opclass 必须匹配 `<=>` 用的 `vector_cosine_ops`）。

**被否掉的方案**：MySQL + Milvus（MySQL 没有对等的行级安全策略，还要在两个系统之间自己搭一致性，**代价最高、拿到的却最少**）、PostgreSQL + Qdrant（分离路线里最合理的一个，但换掉了同库同事务和引擎层权限过滤这两条白拿的性质）、Chroma / FAISS（嵌入式形态，索引跟着进程走，多实例部署时每个实例各带一份，上线形态本来就是多实例）。

> ⚠️ 顺带说一句：**「MySQL + pgvector」这个组合不存在**——pgvector 是 PostgreSQL 的扩展，不是能挂到 MySQL 上的插件。

## 📁 项目结构

```
├── app/
│   ├── main.py              # FastAPI 入口，注册 7 个 Router
│   ├── config.py            # 配置管理（.env → Settings）
│   ├── database.py          # SQLAlchemy 引擎与 Session
│   ├── mcp_server.py        # MCP Server（查询能力对外标准化出口）
│   ├── models/              # 11 张数据表 ORM 模型（业务 8 张 + 向量/口径 3 张）
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
│       ├── memory_store.py    # 业务口径记忆（三层作用域 + 改口版本化 + 分池淘汰）
│       └── rag/             # RAG 管线（加载/切分/向量化/检索/生成）
├── frontend/                # Vue 3 SPA
├── scripts/                 # 初始化、模拟数据、测试脚本
├── eval.py                  # 四维度 Agent 评估
├── eval_dataset.json        # 评估数据集
├── data/
│   └── documents/           # RAG 制度文档（向量落在 PostgreSQL 的 doc_chunks 表里）
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
- **Agent 持久化** — 会话状态落 Postgres 的独立库 `agent_checkpoints`（`AsyncPostgresSaver`），服务重启不丢对话历史、多实例共享；**连不上就起不来，没有降级**（启动时预热，失败即启动失败）

## 📄 License

MIT

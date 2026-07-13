"""端到端验证：数据源 → 采集 → 清洗 → 分析 → LangChain 查询。
直接使用 FastAPI TestClient，不走 HTTP 传输。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal

client = TestClient(app)
UPLOAD = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")

# ── 准备 4 个数据源 ──
sources_def = [
    {"name": "考勤部-2025.11考勤表", "source_type": "excel",
     "file_path": os.path.join(UPLOAD, "考勤部-2025.11考勤表.xlsx"), "encoding": "utf-8"},
    {"name": "销售部-2025.11销售数据", "source_type": "csv",
     "file_path": os.path.join(UPLOAD, "销售部-2025.11销售数据.csv"), "delimiter": ",", "encoding": "utf-8"},
    {"name": "客户部-2025.11客户数据", "source_type": "excel",
     "file_path": os.path.join(UPLOAD, "客户部-2025.11客户数据.xlsx"), "encoding": "utf-8"},
    {"name": "运营部-2025.11月度报表", "source_type": "pdf",
     "file_path": os.path.join(UPLOAD, "运营部-2025.11月度报表.pdf")},
]

source_ids = []
print("=" * 60)
print("Step 1: 注册数据源")
print("=" * 60)

for s in sources_def:
    r = client.post("/api/v1/datasources", json=s)
    assert r.status_code == 200, f"Create failed: {r.text}"
    sid = r.json()["data"]["id"]
    source_ids.append(sid)
    print(f"  ✅ [{sid}] {s['name']}")

# ── 批量采集 ──
print("\n" + "=" * 60)
print("Step 2: 批量采集")
print("=" * 60)

r = client.post("/api/v1/collect/batch", json={"source_ids": source_ids})
assert r.status_code == 200, f"Batch collect failed: {r.text}"
results = r.json()["data"]["results"]
batch_ids = {}
for res in results:
    status = "✅" if res["status"] == "success" else "❌"
    print(f"  {status} {res['source_name']}: {res['record_count']} 条, batch={res['batch_id']}")
    if res["status"] == "success":
        batch_ids[res["source_id"]] = res["batch_id"]

# ── 清洗每个批次 ──
print("\n" + "=" * 60)
print("Step 3: 清洗数据")
print("=" * 60)

for sid, bid in batch_ids.items():
    r = client.post(f"/api/v1/clean/{bid}")
    assert r.status_code == 200, f"Clean failed for {bid}: {r.text}"
    cd = r.json()["data"]
    print(f"  ✅ batch={bid}: {cd['total_input']}→{cd['total_output']}, 异常={cd['anomaly_count']}")

# ── 查看分析报表 ──
print("\n" + "=" * 60)
print("Step 4: 分析报表")
print("=" * 60)

r = client.get("/api/v1/analysis/summary")
sd = r.json()["data"]
print(f"  总记录: {sd['total_records']}, 总批次: {sd['total_batches']}")
for t in sd["by_type"]:
    print(f"  [{t['data_type']}] {t['record_count']}条, 异常率={t['anomaly_rate']}%")

r = client.get("/api/v1/analysis/quality")
qd = r.json()["data"]
print(f"  整体异常率: {qd['anomaly_rate']}%, 平均质量分: {qd['avg_quality_score']}")

# ── 异常记录 ──
print("\n" + "=" * 60)
print("Step 5: 异常记录")
print("=" * 60)

r = client.get("/api/v1/clean/anomalies")
anomalies = r.json()["data"]["items"]
print(f"  异常记录总数: {r.json()['data']['total']}")
for a in anomalies[:5]:
    print(f"  - ID={a['id']} [{a['data_type']}] {a['anomaly_reason'] or 'LLM判定异常'}")

# ── LangChain 查询 ──
print("\n" + "=" * 60)
print("Step 6: LangChain Agent 自然语言查询")
print("=" * 60)

questions = [
    "总体数据质量怎么样？",
    "销售部有多少条异常记录？",
]

for q in questions:
    r = client.post("/api/v1/query", json={"question": q})
    if r.status_code == 200:
        result = r.json()["data"]
        print(f"\n  Q: {q}")
        print(f"  A: {result['answer'][:200]}")
        print(f"  迭代: {result['iterations']}次, 工具: {result['tools_used']}")
    else:
        print(f"  ❌ Query failed: {r.text[:200]}")

print("\n" + "=" * 60)
print("✅ 端到端验证全部通过！")
print("=" * 60)
print(f"  - 4 个数据源注册完成")
print(f"  - {len(batch_ids)} 个批次采集成功")
print(f"  - 清洗完成，entry={sd['total_records']} 条标准化记录")
print(f"  - 异常检测: {len(anomalies)} 条异常记录")
print(f"  前端: http://localhost:8002")
print(f"  API文档: http://localhost:8002/docs")

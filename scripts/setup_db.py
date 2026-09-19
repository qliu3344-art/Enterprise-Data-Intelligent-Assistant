"""一键建库建表 + 创建数据源 + 采集 + 清洗（端到端验证脚本）。

用法：
    python scripts/setup_db.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

BASE = "http://localhost:8002/api/v1"


def step(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


# ── Step 0: 创建数据库 ──
step("Step 0: 创建数据库")
import pymysql
conn = pymysql.connect(host="localhost", user="root", password="liuqi..2003")
conn.cursor().execute("CREATE DATABASE IF NOT EXISTS data_processing_platform CHARACTER SET utf8mb4")
conn.close()
print("✅ 数据库已就绪")

# ── Step 1: 建表 ──
step("Step 1: 创建数据表")
from app.database import engine, Base
from app.models import DataSource, RawRecord, CleanedRecord, SchemaMapping, PipelineLog
Base.metadata.create_all(bind=engine)
print("✅ 5 张表创建完成")

# ── Step 2: 注册 4 个数据源 ──
step("Step 2: 注册数据源")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sources = [
    {
        "name": "考勤部-2025.11考勤表",
        "source_type": "excel",
        "file_path": os.path.join(BASE_DIR, "data", "uploads", "考勤部-2025.11考勤表.xlsx"),
        "encoding": "utf-8",
    },
    {
        "name": "销售部-2025.11销售数据",
        "source_type": "csv",
        "file_path": os.path.join(BASE_DIR, "data", "uploads", "销售部-2025.11销售数据.csv"),
        "delimiter": ",",
        "encoding": "utf-8",
    },
    {
        "name": "客户部-2025.11客户数据",
        "source_type": "excel",
        "file_path": os.path.join(BASE_DIR, "data", "uploads", "客户部-2025.11客户数据.xlsx"),
        "encoding": "utf-8",
    },
    {
        "name": "运营部-2025.11月度报表",
        "source_type": "pdf",
        "file_path": os.path.join(BASE_DIR, "data", "uploads", "运营部-2025.11月度报表.pdf"),
    },
]

source_ids = []
for s in sources:
    r = requests.post(f"{BASE}/datasources", json=s)
    data = r.json()
    sid = data["data"]["id"]
    source_ids.append(sid)
    print(f"  ✅ [{sid}] {s['name']}")

# ── Step 3: 批量采集 ──
step("Step 3: 批量采集")
r = requests.post(f"{BASE}/collect/batch", json={"source_ids": source_ids})
data = r.json()
results = data["data"]["results"]
for res in results:
    status = "✅" if res["status"] == "success" else "❌"
    print(f"  {status} {res['source_name']}: {res['record_count']} 条, batch={res['batch_id']}")
    if res["status"] == "success":
        # Step 4: 清洗
        step(f"Step 4: 清洗 {res['source_name']}")
        r2 = requests.post(f"{BASE}/clean/{res['batch_id']}")
        cdata = r2.json()
        cd = cdata["data"]
        print(f"  输入: {cd['total_input']} → 输出: {cd['total_output']}, 异常: {cd['anomaly_count']}")

# ── Summary ──
step("Step 5: 验证结果")
r = requests.get(f"{BASE}/analysis/summary")
sdata = r.json()["data"]
print(f"  总记录数: {sdata['total_records']}")
print(f"  总批次数: {sdata['total_batches']}")
for t in sdata["by_type"]:
    print(f"  [{t['data_type']}] {t['record_count']}条, 异常率={t['anomaly_rate']}%")

r2 = requests.get(f"{BASE}/clean/anomalies")
anomalies = r2.json()["data"]["items"]
print(f"\n  异常记录: {len(anomalies)} 条")
for a in anomalies[:3]:
    print(f"  - ID={a['id']}, {a['data_type']}, {a['anomaly_reason'] or 'LLM判定'}")

print(f"\n{'='*60}")
print(f"  ✅ 端到端验证完成！")
print(f"{'='*60}")
print(f"  前端: http://localhost:8002")
print(f"  API文档: http://localhost:8002/docs")

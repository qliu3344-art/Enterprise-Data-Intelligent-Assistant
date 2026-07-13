"""全面交互测试：覆盖所有 API 端点 + 验证 Agent 自然语言输出。
用法：python scripts/test_full_interaction.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
PASS, FAIL = 0, 0
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")

def check(step, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {step}{' — ' + detail if detail else ''}")
    else:
        FAIL += 1
        print(f"  ❌ {step}{' — ' + detail if detail else ''}")

# ==========================================================
print("=" * 60)
print("1. 数据源管理 CRUD")
print("=" * 60)

# 1a. 创建数据源
r = client.post("/api/v1/datasources", json={
    "name": "测试-考勤表",
    "source_type": "excel",
    "file_path": os.path.join(UPLOAD_DIR, "考勤部-2025.11考勤表.xlsx"),
    "encoding": "utf-8",
})
check("POST /datasources (创建)", r.status_code == 200, f"id={r.json()['data']['id']}")
src_id = r.json()["data"]["id"]

# 1b. 列表查询
r = client.get("/api/v1/datasources")
check("GET /datasources (列表)", r.status_code == 200, f"total={r.json()['data']['total']}")

# 1c. 详情
r = client.get(f"/api/v1/datasources/{src_id}")
check("GET /datasources/:id (详情)", r.status_code == 200)

# 1d. 更新
r = client.put(f"/api/v1/datasources/{src_id}", json={"name": "测试-考勤表-已更新"})
check("PUT /datasources/:id (更新)", r.status_code == 200)

# 1e. 测试连接
r = client.post(f"/api/v1/datasources/{src_id}/test")
d = r.json()["data"]
check("POST /datasources/:id/test (测试连接)", d["success"], f"rows={d['row_count']}, cols={len(d.get('columns', []))}")
test_cols = d.get("columns", [])

# ==========================================================
print("\n" + "=" * 60)
print("2. LLM 表头对齐")
print("=" * 60)

r = client.post(f"/api/v1/datasources/{src_id}/align")
d = r.json()["data"]
check("POST /datasources/:id/align (LLM对齐)", d["success"], f"confidence={d['confidence']}, mapped={len(d['mapping'])}")
check("  映射了全部列", len(d["unmapped"]) == 0, f"unmapped={d['unmapped']}")
check("  置信度 >= 0.8", d["confidence"] >= 0.8)

# ==========================================================
print("\n" + "=" * 60)
print("3. 数据采集")
print("=" * 60)

# 3a. 单源采集
r = client.post(f"/api/v1/collect/{src_id}")
check("POST /collect/:id (单源采集)", r.status_code == 200, f"records={r.json()['data']['record_count']}")
batch_id = r.json()["data"]["batch_id"]

# 3b. 采集历史
r = client.get("/api/v1/collect/history")
check("GET /collect/history (采集历史)", r.status_code == 200, f"total={r.json()['data']['total']}")

# 3c. 批次详情
r = client.get(f"/api/v1/collect/{batch_id}")
check("GET /collect/:batchId (批次详情)", r.status_code == 200, f"records={r.json()['data']['total_records']}")

# ==========================================================
print("\n" + "=" * 60)
print("4. 数据清洗")
print("=" * 60)

# 4a. 触发清洗
r = client.post(f"/api/v1/clean/{batch_id}")
d = r.json()["data"]
check("POST /clean/:batchId (触发清洗)", r.status_code == 200, f"in={d['total_input']}→out={d['total_output']}, anomalies={d['anomaly_count']}")

# 4b. 清洗日志
r = client.get(f"/api/v1/clean/{batch_id}/logs")
check("GET /clean/:batchId/logs (清洗日志)", r.status_code == 200, f"steps={len(r.json()['data']['items'])}")

# 4c. 异常列表
r = client.get("/api/v1/clean/anomalies")
anomalies_data = r.json()["data"]
check("GET /clean/anomalies (异常列表)", r.status_code == 200, f"total={anomalies_data['total']}")

# 4d. 异常审核（人工交互）
if anomalies_data["total"] > 0:
    first_anomaly = anomalies_data["items"][0]
    aid = first_anomaly["id"]
    # 标记为正常
    r = client.put(f"/api/v1/clean/anomalies/{aid}", json={
        "is_anomaly": False,
        "anomaly_reason": "人工审核：属于双11加班，业务合理",
    })
    check(f"PUT /clean/anomalies/{aid} (标记正常)", r.status_code == 200)
    # 再标记回异常
    r = client.put(f"/api/v1/clean/anomalies/{aid}", json={
        "is_anomaly": True,
        "anomaly_reason": "人工审核确认：数据超常，需业务确认",
    })
    check(f"PUT /clean/anomalies/{aid} (确认异常)", r.status_code == 200)

# ==========================================================
print("\n" + "=" * 60)
print("5. 分析报表")
print("=" * 60)

r = client.get("/api/v1/analysis/summary")
s = r.json()["data"]
check("GET /analysis/summary (汇总)", r.status_code == 200, f"records={s['total_records']}, types={len(s['by_type'])}")

r = client.get("/api/v1/analysis/trend?metric=record_count&granularity=month")
check("GET /analysis/trend (趋势)", r.status_code == 200, f"points={len(r.json()['data']['data_points'])}")

r = client.get("/api/v1/analysis/quality")
check("GET /analysis/quality (质量)", r.status_code == 200, f"anomaly_rate={r.json()['data']['anomaly_rate']}%")

r = client.post("/api/v1/analysis/export", json={"include_anomalies": True})
check("POST /analysis/export (导出)", r.status_code == 200, f"file={r.json()['data']['filename']}")

# ==========================================================
print("\n" + "=" * 60)
print("6. LangChain Agent 自然语言查询（重点验证输出格式）")
print("=" * 60)

questions = [
    "总体数据质量怎么样？",
    "考勤部有多少条异常记录？原因是什么？",
    "销售部的数据情况如何？",
    "哪个部门数据质量最好？",
]

for q in questions:
    r = client.post("/api/v1/query", json={"question": q})
    assert r.status_code == 200, f"Query failed: {r.text}"
    d = r.json()["data"]
    answer = d["answer"]
    bad = answer.strip().startswith("{") or answer.strip().startswith("```")
    has_json_syntax = "{" in answer and "}" in answer and '"总记录数"' in answer

    # 兼容新旧响应格式
    mode = d.get("mode", "agent")
    if mode == "agent":
        tools = d.get("tools_used", d.get("agent_data", {}).get("tools_used", []))
    elif mode == "rag":
        tools = ["（RAG 检索）"]
    else:
        tools = d.get("agent_data", {}).get("tools_used", ["（混合模式）"])
    check(f"Query[{mode}]: {q[:20]}...", not bad and not has_json_syntax,
          f"tools={tools}, ans={answer[:80]}...")

# ==========================================================
print("\n" + "=" * 60)
print("7. 数据源删除")
print("=" * 60)

r = client.delete(f"/api/v1/datasources/{src_id}")
check("DELETE /datasources/:id (删除)", r.status_code == 200)

r = client.get(f"/api/v1/datasources/{src_id}")
check("  删除后查不到", r.json()["code"] == 404)

# ==========================================================
print("\n" + "=" * 60)
print(f"测试结果: {PASS} 通过, {FAIL} 失败, 共 {PASS+FAIL} 项")
print("=" * 60)

if FAIL > 0:
    sys.exit(1)
else:
    print("✅ 全部交互测试通过！")

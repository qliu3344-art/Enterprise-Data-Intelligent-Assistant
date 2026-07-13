"""测试 _json_to_text 转换效果"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Clear cache
for mod in list(sys.modules.keys()):
    if 'query_agent' in mod:
        del sys.modules[mod]

from app.services.query_agent import _json_to_text

# 模拟 Agent 返回的原始 JSON（跟用户截图中一模一样的结构）
fake = {
    "total_anomalies": 10,
    "showing": 10,
    "anomalies": [
        {
            "id": 48,
            "employee_name": "未知",
            "department": "未知",
            "data_type": "attendance",
            "record_date": "未知",
            "reason": "超过Q3+1.5×IQR，加班31.7小时远超均值11.12，属显著离群",
            "business_data": {
                "姓名": "郑十",
                "出勤天数": 20.2,
                "员工编号": "EMP008",
                "所属部门": "技术部",
                "考勤日期": "2025/11/27",
                "加班时长(小时)": 31.7
            }
        },
        {
            "id": 140,
            "employee_name": "未知",
            "department": "未知",
            "data_type": "attendance",
            "record_date": "未知",
            "reason": "加班33.6小时与历史分布显著偏离，>2个标准差",
            "business_data": {
                "姓名": "陈十二",
                "出勤天数": 19.1,
                "员工编号": "EMP010",
                "所属部门": "客服部",
                "考勤日期": "2025/11/01",
                "加班时长(小时)": 33.6
            }
        }
    ]
}

raw = json.dumps(fake, ensure_ascii=False)
print("=== Input (raw JSON) ===")
print(raw[:150] + "...")
print()

result = _json_to_text(raw)
print("=== Output (converted) ===")
print(result)
print()
print("Starts with '{'?", result.strip().startswith("{"))
print("Contains Chinese?", any('一' <= c <= '鿿' for c in result))

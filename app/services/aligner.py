"""异构表头语义对齐 — LLM 驱动的字段自动映射。

核心功能：将不同部门各自命名的表头（"员工编号"/"工号"/"EmployeeID"）
自动映射到统一标准字段（employee_id）。
"""

import json

from dashscope import Generation

from app.config import settings
from app.logger import logger

# —— 标准字段定义 ——
# 公共维度（所有数据类型都可能有）
COMMON_FIELDS = {
    "employee_name": "员工姓名",
    "employee_id": "工号",
    "department": "部门",
    "record_date": "业务日期",
}

# 各业务类型特有字段
TYPE_SPECIFIC_FIELDS = {
    "attendance": {
        "attendance_days": "出勤天数",
        "leave_days": "请假天数",
        "overtime_hours": "加班时长(小时)",
        "late_count": "迟到次数",
    },
    "sales": {
        "order_amount": "订单金额",
        "order_count": "订单数量",
        "product_name": "产品名称",
        "customer_name": "客户名称",
        "payment_method": "支付方式",
    },
    "customer": {
        "customer_name": "客户名称",
        "customer_level": "客户等级",
        "contract_amount": "合同金额",
        "contact_phone": "联系电话",
        "follow_up_date": "最后跟进日期",
    },
    "operation": {
        "metric_name": "指标名称",
        "metric_value": "指标数值",
        "metric_unit": "指标单位",
        "target_value": "目标值",
        "completion_rate": "完成率",
    },
}

# 全量标准字段（供 LLM prompt 使用）
ALL_STANDARD_FIELDS = {}
ALL_STANDARD_FIELDS.update(COMMON_FIELDS)
for fields in TYPE_SPECIFIC_FIELDS.values():
    ALL_STANDARD_FIELDS.update(fields)

ALIGN_PROMPT = """你是一个数据标准化专家。请将以下数据源的原始列名映射到标准字段名。

## 标准字段定义
{standard_fields}

## 数据类型提示
{data_type_hint}

## 原始列名（来自数据源）
{source_headers}

## 要求
1. 逐个分析每个原始列名，判断它最可能对应的标准字段
2. 如果原始列名与某个标准字段的中文含义相近，就映射到该标准字段名
3. 如果无法对应任何标准字段，将原始值保留到 "unmapped" 列表
4. 对整个映射给出 confidence 评分（0-1），表示你对映射结果的确信程度
5. 返回 JSON 格式：{{"mapping": {{"原始列名": "标准字段key"}}, "unmapped": ["无法映射的原始列名"], "confidence": 0.95}}
6. mapping 中的 key 必须是原始列名，value 必须是标准字段的 key（如 "employee_id"）
7. 只输出 JSON，不要其他内容
"""


def align_headers(
    source_headers: list[str],
    data_type_hint: str = "",
) -> dict:
    """将数据源的原始列名对齐到标准 schema。

    Args:
        source_headers: 原始表头列表，如 ["员工编号", "姓名", "出勤天数"]
        data_type_hint: 数据类型提示，如 "attendance" / "sales" / "customer" / "operation"

    Returns:
        {
            "mapping": {"员工编号": "employee_id", ...},
            "unmapped": [...],
            "confidence": 0.95
        }
    """
    # 根据 data_type_hint 筛选相关标准字段，提高 LLM 准确率
    relevant_fields = dict(COMMON_FIELDS)
    if data_type_hint and data_type_hint in TYPE_SPECIFIC_FIELDS:
        relevant_fields.update(TYPE_SPECIFIC_FIELDS[data_type_hint])
    else:
        # 无类型提示时使用全部字段
        relevant_fields = ALL_STANDARD_FIELDS

    prompt = ALIGN_PROMPT.format(
        standard_fields=json.dumps(relevant_fields, ensure_ascii=False, indent=2),
        data_type_hint=data_type_hint or "未知",
        source_headers=json.dumps(source_headers, ensure_ascii=False),
    )

    resp = Generation.call(
        model=settings.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        result_format="message",
        temperature=0.1,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"LLM 调用失败: {resp.message}")

    content = resp.output.choices[0].message.content.strip()

    # 清理 markdown 代码块
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        logger.error(f"LLM 返回非 JSON: {content[:300]}")
        raise RuntimeError("LLM 返回格式异常，无法解析")

    # 标准化返回结构
    mapping = result.get("mapping", result.get("映射结果", {}))
    unmapped = result.get("unmapped", [])
    confidence = result.get("confidence", 0.5)

    logger.info(
        f"表头对齐完成: headers={len(source_headers)}, "
        f"mapped={len(mapping)}, unmapped={len(unmapped)}, "
        f"confidence={confidence}"
    )

    return {
        "mapping": mapping,
        "unmapped": unmapped,
        "confidence": confidence,
    }


def get_standard_field_description(field_key: str) -> str:
    """获取标准字段的中文说明。"""
    return ALL_STANDARD_FIELDS.get(field_key, field_key)

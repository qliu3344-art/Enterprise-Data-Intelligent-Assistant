"""数据查询核心逻辑 — 与协议无关的纯查询函数。

同一套查询逻辑被两种协议出口复用：
  - LangChain @tool（query_agent.py，对内给自有 Agent）
  - MCP @mcp.tool（app/mcp_server.py，对外给任意 MCP 客户端）

本模块无 LangChain / 无 MCP 依赖，只依赖 SQLAlchemy 模型。
"""


def _get_db():
    """获取线程安全的数据库会话（用于查询函数内部）。"""
    from app.database import SessionLocal
    return SessionLocal()


# 部门值散落在 business_data 的多个键下，键名取决于数据源与是否经过表头对齐：
#   所属部门   考勤（原始中文表头批次）
#   department 考勤（经 aligner 对齐为标准字段后的批次）
#   部门       销售
#   Dept       客户
# 库里的 department 列始终为空，因此筛选、分组、展示都必须回退到这些键。
# 运营表没有部门字段，落在 _record_department 的空串分支。
DEPARTMENT_KEYS = ("department", "所属部门", "部门", "Dept")


def _record_department(record) -> str:
    """取单条记录的部门：优先标准列，再回退 business_data 的各个已知键。"""
    if record.department:
        return record.department
    business_data = record.business_data or {}
    for key in DEPARTMENT_KEYS:
        value = business_data.get(key)
        if value:
            return str(value)
    return ""


def _department_condition(model, department: str):
    """构造部门筛选的 SQL 条件，覆盖标准列与 business_data 的全部已知键。"""
    from sqlalchemy import or_

    conditions = [model.department == department]
    for key in DEPARTMENT_KEYS:
        conditions.append(model.business_data[key].as_string() == department)
    return or_(*conditions)


# 姓名值同样散落在多个键下，键名随数据源变化：
#   employee_name 考勤（经 aligner 对齐为标准字段后的批次）
#   姓名           考勤（原始中文表头批次）
#   销售员         销售
#   Name           客户
# 运营表没有姓名字段，落在 _record_name 的空串分支。
NAME_KEYS = ("employee_name", "姓名", "销售员", "Name")


def _record_name(record) -> str:
    """取单条记录的姓名：优先标准列，再回退 business_data 的各个已知键。"""
    if record.employee_name:
        return record.employee_name
    business_data = record.business_data or {}
    for key in NAME_KEYS:
        value = business_data.get(key)
        if value:
            return str(value)
    return ""


def _name_condition(model, employee_name: str):
    """构造姓名筛选的 SQL 条件，覆盖标准列与 business_data 的全部已知键。"""
    from sqlalchemy import or_

    conditions = [model.employee_name == employee_name]
    for key in NAME_KEYS:
        conditions.append(model.business_data[key].as_string() == employee_name)
    return or_(*conditions)


# 日期值同样散落在多个键下，且 business_data 里的日期是字符串、两种格式混用：
#   record_date / 考勤日期  考勤，写作 2025/11/01（斜杠）
#   日期                    销售，写作 2025-11-20（破折号）
#   Date                    客户 / 运营，写作 2025-11-01（破折号）
# 斜杠与破折号直接比会判错——'/' 的码位(0x2F)大于 '-'(0x2D)，
# '2025/11/30' <= '2025-11-30' 会得到 False。所以比较前统一归一化分隔符。
DATE_KEYS = ("record_date", "考勤日期", "日期", "Date")


def _record_date(record) -> str:
    """取单条记录的日期（YYYY-MM-DD）：优先标准列，再回退 business_data 的各个键。"""
    if record.record_date:
        return record.record_date.isoformat()
    business_data = record.business_data or {}
    for key in DATE_KEYS:
        value = business_data.get(key)
        if value:
            return str(value).replace("/", "-")
    return ""


def _date_condition(model, date_start: str, date_end: str):
    """构造日期范围筛选条件，覆盖标准列与 business_data 的全部已知键。

    每个候选来源内部先满足 [start, end] 整个区间，再跨来源取并集——
    否则「>= start」和「<= end」会被 OR 拆开，匹配到区间外的记录。
    """
    from sqlalchemy import and_, func, or_

    sources = [model.record_date]
    for key in DATE_KEYS:
        # 归一化分隔符后再比，否则斜杠格式的字典序会判错
        sources.append(func.replace(model.business_data[key].as_string(), "/", "-"))

    conditions = []
    for source in sources:
        bounds = []
        if date_start:
            bounds.append(source >= date_start)
        if date_end:
            bounds.append(source <= date_end)
        conditions.append(and_(*bounds))
    return or_(*conditions)


def query_cleaned_records(
    data_type: str = "",
    department: str = "",
    date_start: str = "",
    date_end: str = "",
    limit: int = 20,
) -> str:
    """查询清洗后的标准化数据记录。可筛选数据类型、部门、日期范围。"""
    from app.models.cleaned_record import CleanedRecord

    db = _get_db()
    try:
        query = db.query(CleanedRecord)
        if data_type:
            query = query.filter(CleanedRecord.data_type == data_type)
        if department:
            query = query.filter(_department_condition(CleanedRecord, department))
        if date_start or date_end:
            query = query.filter(_date_condition(CleanedRecord, date_start, date_end))

        total = query.count()
        records = query.limit(limit).all()

        if not records:
            return "查询结果：没有匹配的数据记录。"

        lines = [f"查询结果：共 {total} 条匹配记录，以下展示前 {min(total, limit)} 条："]
        for i, r in enumerate(records, 1):
            name = _record_name(r) or "未知"
            dept = _record_department(r) or "未知"
            dtype = r.data_type or "未知"
            date = _record_date(r) or "未知"
            flag = "⚠异常" if r.is_anomaly else "正常"
            reason = f"，原因：{r.anomaly_reason}" if r.is_anomaly and r.anomaly_reason else ""
            biz = ""
            if r.business_data:
                biz_items = [f"{k}={v}" for k, v in list(r.business_data.items())[:5]]
                biz = f"，业务数据：{'，'.join(biz_items)}"
            lines.append(f"  {i}. [{dtype}] {name}（{dept}）{date} {flag}{reason}{biz}")
        return "\n".join(lines)
    finally:
        db.close()


def get_summary_stats(data_type: str = "") -> str:
    """获取汇总统计数据。返回各类型的记录数、异常率、平均质量分。"""
    from app.models.cleaned_record import CleanedRecord

    db = _get_db()
    try:
        query = db.query(CleanedRecord)
        if data_type:
            query = query.filter(CleanedRecord.data_type == data_type)

        records = query.all()
        if not records:
            return "暂无数据可统计。请先采集并清洗数据。"

        total = len(records)
        anomaly_count = sum(1 for r in records if r.is_anomaly)
        anomaly_rate = round(anomaly_count / total * 100, 2)
        avg_quality = round(sum(r.quality_score or 1.0 for r in records) / total, 4)

        type_groups: dict = {}
        for r in records:
            dt = r.data_type or "unknown"
            if dt not in type_groups:
                type_groups[dt] = {"total": 0, "anomalies": 0, "score_sum": 0.0}
            type_groups[dt]["total"] += 1
            if r.is_anomaly:
                type_groups[dt]["anomalies"] += 1
            type_groups[dt]["score_sum"] += r.quality_score or 1.0

        dept_groups: dict = {}
        for r in records:
            dept = _record_department(r) or "未知部门"
            if dept not in dept_groups:
                dept_groups[dept] = {"total": 0, "anomalies": 0}
            dept_groups[dept]["total"] += 1
            if r.is_anomaly:
                dept_groups[dept]["anomalies"] += 1

        TYPE_NAMES = {"attendance": "考勤", "sales": "销售", "customer": "客户", "operation": "运营"}

        lines = [
            "数据统计报告：",
            f"  总记录数：{total} 条",
            f"  异常记录数：{anomaly_count} 条",
            f"  整体异常率：{anomaly_rate}%",
            f"  平均质量分：{avg_quality}",
            "",
            "按数据类型分布：",
        ]
        for dt in ["attendance", "sales", "customer", "operation"]:
            if dt in type_groups:
                g = type_groups[dt]
                ar = round(g["anomalies"] / g["total"] * 100, 2)
                ascore = round(g["score_sum"] / g["total"], 4)
                lines.append(f"  {TYPE_NAMES.get(dt, dt)}：{g['total']} 条，异常 {g['anomalies']} 条（{ar}%），质量分 {ascore}")

        lines.append("")
        lines.append("按部门分布：")
        for dept, g in sorted(dept_groups.items()):
            ar = round(g["anomalies"] / g["total"] * 100, 2)
            lines.append(f"  {dept}：{g['total']} 条，异常率 {ar}%")

        return "\n".join(lines)
    finally:
        db.close()


def get_anomaly_details(
    data_type: str = "",
    department: str = "",
    employee_name: str = "",
    limit: int = 10,
) -> str:
    """查询异常记录的详细信息，包括异常原因和业务数据。

    可按数据类型、部门、员工姓名筛选。查具体人时用 employee_name 参数。
    """
    from app.models.cleaned_record import CleanedRecord


    db = _get_db()
    try:
        query = db.query(CleanedRecord).filter(CleanedRecord.is_anomaly == True)
        if data_type:
            query = query.filter(CleanedRecord.data_type == data_type)
        if department:
            query = query.filter(_department_condition(CleanedRecord, department))
        if employee_name:
            query = query.filter(_name_condition(CleanedRecord, employee_name))

        total = query.count()
        records = query.limit(limit).all()

        if not records:
            return "异常记录查询结果：没有找到匹配的异常记录，数据质量良好。"

        TYPE_NAMES = {"attendance": "考勤", "sales": "销售", "customer": "客户", "operation": "运营"}

        lines = [f"异常记录报告：共 {total} 条异常，以下展示前 {min(total, limit)} 条："]
        for i, r in enumerate(records, 1):
            name = _record_name(r) or "未知"
            dept = _record_department(r) or "未知"
            dt = TYPE_NAMES.get(r.data_type, r.data_type or "未知")
            date = _record_date(r) or "未知"
            reason = r.anomaly_reason or "LLM 判定为异常"
            if len(reason) > 120:
                reason = reason[:120] + "..."

            lines.append(f"  {i}. [{dt}] {name}（{dept}）{date}")
            lines.append(f"     异常原因：{reason}")

            if r.business_data:
                biz_items = []
                for k, v in list(r.business_data.items())[:5]:
                    biz_items.append(f"{k}={v}")
                lines.append(f"     业务数据：{'，'.join(biz_items)}")

        return "\n".join(lines)
    finally:
        db.close()

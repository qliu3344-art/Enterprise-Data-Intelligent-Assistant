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
            query = query.filter(
                (CleanedRecord.department == department) |
                (CleanedRecord.business_data['所属部门'].as_string() == department)
            )
        if date_start:
            query = query.filter(CleanedRecord.record_date >= date_start)
        if date_end:
            query = query.filter(CleanedRecord.record_date <= date_end)

        total = query.count()
        records = query.limit(limit).all()

        if not records:
            return "查询结果：没有匹配的数据记录。"

        lines = [f"查询结果：共 {total} 条匹配记录，以下展示前 {min(total, limit)} 条："]
        for i, r in enumerate(records, 1):
            name = r.employee_name or "未知"
            dept = r.department or "未知"
            dtype = r.data_type or "未知"
            date = str(r.record_date) if r.record_date else "未知"
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
            dept = r.department or "未知部门"
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
            query = query.filter(
                (CleanedRecord.department == department) |
                (CleanedRecord.business_data['所属部门'].as_string() == department)
            )
        if employee_name:
            # 同时搜索 employee_name 列和 business_data JSON 中的姓名
            query = query.filter(
                (CleanedRecord.employee_name == employee_name) |
                (CleanedRecord.business_data['姓名'].as_string() == employee_name)
            )

        total = query.count()
        records = query.limit(limit).all()

        if not records:
            return "异常记录查询结果：没有找到匹配的异常记录，数据质量良好。"

        TYPE_NAMES = {"attendance": "考勤", "sales": "销售", "customer": "客户", "operation": "运营"}

        lines = [f"异常记录报告：共 {total} 条异常，以下展示前 {min(total, limit)} 条："]
        for i, r in enumerate(records, 1):
            name = r.employee_name or "未知"
            dept = r.department or "未知"
            dt = TYPE_NAMES.get(r.data_type, r.data_type or "未知")
            date = str(r.record_date) if r.record_date else "未知"
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

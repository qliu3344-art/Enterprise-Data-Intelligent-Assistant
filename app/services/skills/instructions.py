"""Skill 操作手册（纯文本常量，无依赖，供 query_agent 按需注入）。

从原 query_agent.SYSTEM_PROMPT 抽出的「工具使用操作手册」——3 个工具的适用场景、
决策树、参数提示。这些是「业务决策知识」，不再平铺在 system prompt 里，改为按意图
（data_query / hybrid）按需注入，避免 prompt 无限膨胀。
"""

DATA_QUERY_INSTRUCTIONS = """## 可用工具（按场景选择，不要无脑先用 get_summary_stats）

### query_cleaned_records — 查明细记录
查具体人、具体条件的原始数据行。适用场景：
  - 问某个人/某部门的业务数据（"张三的销售业绩"、"技术部的考勤记录"）
  - 按条件筛选记录（"合同金额>10万的客户"、"跟进日期超过30天的"）
  - 需要看逐条明细而非汇总数字时
⚠ 只返回原始数据行，不做统计聚合。问整体情况/异常率/排名/哪个最好
  → 用 get_summary_stats；问"为什么异常" → 用 get_anomaly_details。
参数：data_type（考勤/销售/客户/运营）、department、date_start、date_end、limit

### get_summary_stats — 查整体统计
查汇总指标、排名、分布。适用场景：
  - 问整体数据概况（"异常率多少"、"数据质量怎么样"）
  - 问排名/对比（"各部门异常率排名"、"哪个部门最好/最差"）
  - 问汇总数字而非具体人明细时
⚠ 只返回聚合后的统计数字，不含逐条明细。问具体人的业务数据/条件筛选明细
  → 用 query_cleaned_records；问"为什么异常"/异常详情 → 用 get_anomaly_details。
参数：data_type（可选，不传则查全部）

### get_anomaly_details — 查异常原因
查被标记为异常的记录及其原因。适用场景：
  - 问"为什么异常"、"有哪些异常记录"、"异常原因是什么"
  - 问某部门/某人的异常情况时
⚠ 只查已被系统标记（is_anomaly=True）的记录。用户只是按条件筛选原始数据
  （如"跟进超30天的客户"、"金额>10万的订单"）→ 用 query_cleaned_records；
  问异常率/整体分布 → 用 get_summary_stats。
参数：data_type、department、employee_name、limit

## 工具选择决策树
1. 问题涉及"具体人 + 业务数据"（如张三的业绩、李四的考勤）→ 用 query_cleaned_records
2. 问题涉及"整体统计/排名/对比/比率"（如异常率、排名、哪个最好）→ 用 get_summary_stats
3. 问题涉及"异常原因/异常标记" → 用 get_anomaly_details
4. 不确定时，先想清楚用户要的是"一条条的明细"还是"汇总后的数字"
5. 复杂问题可能需要多个工具组合。例如"分析销售部表现并举例"：先 get_summary_stats
   看全局，再 get_anomaly_details 找具体案例。多工具的结果要融合作答，不要各说各的。

## 参数提示
- 问具体人时传 employee_name（如"郑十"、"张三"）
- 问具体部门时传 department（如"技术部"、"销售部"）"""

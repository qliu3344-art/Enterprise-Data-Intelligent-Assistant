"""初始化数据库：创建所有表。

用法：
    python scripts/init_db.py
"""

import sys
import os

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine, Base
from app.models import (  # noqa: F401
    DataSource,
    RawRecord,
    CleanedRecord,
    SchemaMapping,
    PipelineLog,
    ChatHistory,
    QueryTrace,
    Feedback,
)


def init():
    print("正在创建数据库表...")
    Base.metadata.create_all(bind=engine)

    # 为已有数据库补列（如果不存在）
    migrations = [
        (
            "schema_mappings",
            "unmapped_columns",
            "ALTER TABLE schema_mappings ADD COLUMN unmapped_columns JSON "
            "COMMENT 'LLM 无法映射的原始列名列表'",
        ),
        # query_trace 可观测性扩展：全链路 trace_id / 工具参数 / 分段耗时 / 意图路由分布
        ("query_trace", "trace_id",
         "ALTER TABLE query_trace ADD COLUMN trace_id VARCHAR(36) DEFAULT '' COMMENT '全链路追踪标识'"),
        ("query_trace", "tool_calls",
         "ALTER TABLE query_trace ADD COLUMN tool_calls JSON COMMENT '工具名+实际参数'"),
        ("query_trace", "stage_timings",
         "ALTER TABLE query_trace ADD COLUMN stage_timings JSON COMMENT '分段耗时（毫秒）'"),
        ("query_trace", "route_path",
         "ALTER TABLE query_trace ADD COLUMN route_path VARCHAR(16) DEFAULT '' COMMENT '意图路由路径 logprobs/rule'"),
        ("query_trace", "route_degrade",
         "ALTER TABLE query_trace ADD COLUMN route_degrade VARCHAR(64) DEFAULT '' COMMENT '路由失败原因分类'"),
        ("query_trace", "p_top",
         "ALTER TABLE query_trace ADD COLUMN p_top FLOAT DEFAULT 0 COMMENT '模型选中该标签的真实概率'"),
        ("query_trace", "label_mass",
         "ALTER TABLE query_trace ADD COLUMN label_mass FLOAT DEFAULT 0 COMMENT '三个合法标签概率之和'"),
        ("query_trace", "route_margin",
         "ALTER TABLE query_trace ADD COLUMN route_margin FLOAT DEFAULT 0 COMMENT 'top1 与 top2 概率差'"),
        ("query_trace", "top_logprobs",
         "ALTER TABLE query_trace ADD COLUMN top_logprobs JSON COMMENT '首 token 的 top5 原始分布'"),
    ]

    try:
        from sqlalchemy import text, inspect
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()
        for table, column, ddl in migrations:
            if table not in existing_tables:
                continue
            cols = [c["name"] for c in inspector.get_columns(table)]
            if column in cols:
                continue
            with engine.connect() as conn:
                conn.execute(text(ddl))
                conn.commit()
            print(f"  ✅ {table}.{column} 列已添加")
    except Exception as e:
        print(f"  ⚠️ 补列迁移跳过: {e}")

    print("✅ 所有表创建完成！")
    print("  - data_sources")
    print("  - raw_records")
    print("  - cleaned_records")
    print("  - schema_mappings")
    print("  - pipeline_logs")
    print("  - chat_history")
    print("  - query_trace")
    print("  - feedback")


if __name__ == "__main__":
    init()

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
)


def init():
    print("正在创建数据库表...")
    Base.metadata.create_all(bind=engine)

    # 为已有数据库添加 unmapped_columns 列（如果不存在）
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(engine)
        if "schema_mappings" in inspector.get_table_names():
            cols = [c["name"] for c in inspector.get_columns("schema_mappings")]
            if "unmapped_columns" not in cols:
                with engine.connect() as conn:
                    conn.execute(text(
                        "ALTER TABLE schema_mappings ADD COLUMN unmapped_columns JSON COMMENT 'LLM 无法映射的原始列名列表'"
                    ))
                    conn.commit()
                print("  ✅ schema_mappings.unmapped_columns 列已添加")
    except Exception as e:
        print(f"  ⚠️ 迁移 unmapped_columns 跳过: {e}")

    print("✅ 所有表创建完成！")
    print("  - data_sources")
    print("  - raw_records")
    print("  - cleaned_records")
    print("  - schema_mappings")
    print("  - pipeline_logs")
    print("  - chat_history")
    print("  - query_trace")


if __name__ == "__main__":
    init()

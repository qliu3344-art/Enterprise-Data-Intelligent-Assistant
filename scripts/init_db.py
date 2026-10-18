"""初始化数据库：创建所有表。

用法：
    python scripts/init_db.py
"""

import sys
import os

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

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
    DocChunk,
    DocIndexState,
    BusinessConvention,
)


def init():
    print("正在创建数据库表...")

    # 向量列 DDL 依赖 vector 类型，扩展必须先于 create_all 建好，
    # 否则报 "type \"vector\" does not exist"。
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    print("  ✅ pgvector 扩展已就绪")

    Base.metadata.create_all(bind=engine)

    # 为已有数据库补列（如果不存在）
    # 每条为 (表, 列, [语句...])。PG 不支持 MySQL 那种行内 COMMENT '...'，
    # 列注释必须拆成独立的 COMMENT ON COLUMN 语句单独执行。
    migrations = [
        (
            "schema_mappings",
            "unmapped_columns",
            [
                "ALTER TABLE schema_mappings ADD COLUMN unmapped_columns JSON",
                "COMMENT ON COLUMN schema_mappings.unmapped_columns IS 'LLM 无法映射的原始列名列表'",
            ],
        ),
        # query_trace 可观测性扩展：全链路 trace_id / 工具参数 / 分段耗时 / 意图路由分布
        ("query_trace", "trace_id",
         ["ALTER TABLE query_trace ADD COLUMN trace_id VARCHAR(36) DEFAULT ''",
          "COMMENT ON COLUMN query_trace.trace_id IS '全链路追踪标识'"]),
        ("query_trace", "tool_calls",
         ["ALTER TABLE query_trace ADD COLUMN tool_calls JSON",
          "COMMENT ON COLUMN query_trace.tool_calls IS '工具名+实际参数'"]),
        ("query_trace", "stage_timings",
         ["ALTER TABLE query_trace ADD COLUMN stage_timings JSON",
          "COMMENT ON COLUMN query_trace.stage_timings IS '分段耗时（毫秒）'"]),
        ("query_trace", "route_path",
         ["ALTER TABLE query_trace ADD COLUMN route_path VARCHAR(16) DEFAULT ''",
          "COMMENT ON COLUMN query_trace.route_path IS '意图路由路径 logprobs/rule'"]),
        ("query_trace", "route_degrade",
         ["ALTER TABLE query_trace ADD COLUMN route_degrade VARCHAR(64) DEFAULT ''",
          "COMMENT ON COLUMN query_trace.route_degrade IS '路由失败原因分类'"]),
        ("query_trace", "p_top",
         ["ALTER TABLE query_trace ADD COLUMN p_top FLOAT DEFAULT 0",
          "COMMENT ON COLUMN query_trace.p_top IS '模型选中该标签的真实概率'"]),
        ("query_trace", "label_mass",
         ["ALTER TABLE query_trace ADD COLUMN label_mass FLOAT DEFAULT 0",
          "COMMENT ON COLUMN query_trace.label_mass IS '三个合法标签概率之和'"]),
        ("query_trace", "route_margin",
         ["ALTER TABLE query_trace ADD COLUMN route_margin FLOAT DEFAULT 0",
          "COMMENT ON COLUMN query_trace.route_margin IS 'top1 与 top2 概率差'"]),
        ("query_trace", "top_logprobs",
         ["ALTER TABLE query_trace ADD COLUMN top_logprobs JSON",
          "COMMENT ON COLUMN query_trace.top_logprobs IS '首 token 的 top5 原始分布'"]),
        # 执行期校验结论：飞轮的标签来源（intent/mode 只说明升过级，说明不了哪段命中）
        ("query_trace", "exec_check",
         ["ALTER TABLE query_trace ADD COLUMN exec_check JSON",
          "COMMENT ON COLUMN query_trace.exec_check IS '执行期校验结论（结构/空集/回指/vocab）'"]),
        # 口径治理：作用域 / 状态 / 有效期。原表只有一个全局池，这六列是把它
        # 拆成「公司级正式 / 部门级 / 候选」三条治理路径的落点。
        # 迁移时 business_conventions 是空表，所以不必回填历史行。
        ("business_conventions", "scope",
         ["ALTER TABLE business_conventions ADD COLUMN scope VARCHAR(16) NOT NULL DEFAULT 'global'",
          "COMMENT ON COLUMN business_conventions.scope IS '作用域：global 公司级 / department 部门级'"]),
        ("business_conventions", "department",
         ["ALTER TABLE business_conventions ADD COLUMN department VARCHAR(64)",
          "COMMENT ON COLUMN business_conventions.department IS '部门作用域；scope=department 时必填'"]),
        ("business_conventions", "status",
         ["ALTER TABLE business_conventions ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'candidate'",
          "COMMENT ON COLUMN business_conventions.status IS 'candidate 候选 / active 已生效'"]),
        ("business_conventions", "session_id",
         ["ALTER TABLE business_conventions ADD COLUMN session_id VARCHAR(64)",
          "COMMENT ON COLUMN business_conventions.session_id IS '候选口径的来源会话；转 active 后置空'"]),
        ("business_conventions", "effective_from",
         ["ALTER TABLE business_conventions ADD COLUMN effective_from DATE",
          "COMMENT ON COLUMN business_conventions.effective_from IS '生效起始日；NULL=不设下界'"]),
        ("business_conventions", "effective_until",
         ["ALTER TABLE business_conventions ADD COLUMN effective_until DATE",
          "COMMENT ON COLUMN business_conventions.effective_until IS '失效日；NULL=仍是当前版本'"]),
    ]

    # 索引不走上面的补列机制（那几个按「列不存在才建」判断，索引没有列可判）。
    # CREATE INDEX IF NOT EXISTS 本身幂等，直接执行。
    index_ddls = [
        "CREATE INDEX IF NOT EXISTS idx_conventions_scope_dept "
        "ON business_conventions (scope, department)",
        "CREATE INDEX IF NOT EXISTS idx_conventions_status "
        "ON business_conventions (status)",
    ]

    try:
        from sqlalchemy import inspect
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()
        for table, column, ddls in migrations:
            if table not in existing_tables:
                continue
            cols = [c["name"] for c in inspector.get_columns(table)]
            if column in cols:
                continue
            with engine.begin() as conn:
                for ddl in ddls:
                    conn.execute(text(ddl))
            print(f"  ✅ {table}.{column} 列已添加")

        if "business_conventions" in existing_tables:
            with engine.begin() as conn:
                for ddl in index_ddls:
                    conn.execute(text(ddl))
            print("  ✅ business_conventions 索引已就绪")
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
    print("  - doc_chunks / doc_index_state（制度条款 + 向量）")
    print("  - business_conventions（业务口径长期记忆）")


if __name__ == "__main__":
    init()

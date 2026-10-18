"""口径候选审核：候选进池、人工裁定。

用户说出来的口径一律先进候选池，而且**当次会话按他说的算**——候选只在它诞生的
那个会话里能被召回。审核决定的是「要不要推广给其他人」，不是「这次算不算数」。
所以这个脚本不参与任何实时链路，它是治理流程的那一半。

机器负责发现，人负责裁定：和评测集「候选进池 → 人工裁定 → 系统回归」是同一个
范式，同一个系统里出现两次，说明它是设计范式而不是临时拼凑。

用法：
    python scripts/review_conventions.py list
    python scripts/review_conventions.py list --scope department
    python scripts/review_conventions.py list --status active --limit 100
    python scripts/review_conventions.py approve 3 4 7
    python scripts/review_conventions.py reject 9
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select, update

from app.database import SessionLocal
from app.models.business_convention import BusinessConvention, STATUS_ACTIVE


def cmd_list(args):
    db = SessionLocal()
    try:
        stmt = select(BusinessConvention).order_by(
            BusinessConvention.last_accessed.desc()
        ).limit(args.limit)
        if args.status:
            stmt = stmt.where(BusinessConvention.status == args.status)
        if args.scope:
            stmt = stmt.where(BusinessConvention.scope == args.scope)

        rows = db.scalars(stmt).all()
        if not rows:
            print("（没有符合条件的口径）")
            return

        for r in rows:
            scope = r.scope + (f":{r.department}" if r.department else "")
            window = f"{r.effective_from or '—'} ~ {r.effective_until or '今'}"
            print(f"[{r.id}] {r.status:<9} {scope:<18} {r.last_accessed:%Y-%m-%d}  {window}")
            print(f"      {r.content}")
        print(f"\n共 {len(rows)} 条")
    finally:
        SessionLocal.remove()


def cmd_approve(args):
    db = SessionLocal()
    try:
        # session_id 一并置空：这条口径转成对所有会话可见之后，
        # 「来源会话」这个字段就没有意义了，留着只会误导后来看库的人。
        result = db.execute(
            update(BusinessConvention)
            .where(BusinessConvention.id.in_(args.ids))
            .values(status=STATUS_ACTIVE, session_id=None)
        )
        db.commit()
        print(f"✅ {result.rowcount} 条转为已生效（其余 id 不存在或已生效）")
        if result.rowcount != len(args.ids):
            print(f"   （给了 {len(args.ids)} 个 id）")
    finally:
        SessionLocal.remove()


def cmd_reject(args):
    db = SessionLocal()
    try:
        result = db.execute(
            delete(BusinessConvention).where(BusinessConvention.id.in_(args.ids))
        )
        db.commit()
        print(f"✅ 删除 {result.rowcount} 条")
    finally:
        SessionLocal.remove()


def main():
    parser = argparse.ArgumentParser(description="业务口径候选审核")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出候选/已生效口径")
    p_list.add_argument("--status", choices=["candidate", "active"], default=None)
    p_list.add_argument("--scope", choices=["global", "department"], default=None)
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)

    p_approve = sub.add_parser("approve", help="把候选转为已生效（推广给所有会话）")
    p_approve.add_argument("ids", type=int, nargs="+")
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="驳回并删除候选")
    p_reject.add_argument("ids", type=int, nargs="+")
    p_reject.set_defaults(func=cmd_reject)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

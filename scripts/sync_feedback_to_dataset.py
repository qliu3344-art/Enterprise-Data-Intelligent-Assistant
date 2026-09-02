"""把点踩反馈同步为评测集候选样本（半自动：ground truth 由人确认）。

工作流（两步，中间夹人工）：
  1. export：拉 feedback 表 rating=-1 的反馈 → 生成 eval_candidates.json 候选文件
     （expected_intent / expected_tools 等字段留空，待人工标注）
  2. 人工：打开候选文件，填 expected_intent / expected_tools / reference_keywords / category，
     把 status 改成 "ready"
  3. merge：把 status=ready 的候选合并进 eval_dataset.json

为什么半自动：反馈只是「坏 case 候选」，不是标准答案——ground truth 必须人工确认，
不能让脚本（或 LLM）自己标 expected_* 再拿这个标准评自己，否则评测失去客观性。

用法:
    python scripts/sync_feedback_to_dataset.py              # export：拉点踩反馈生成候选
    python scripts/sync_feedback_to_dataset.py --merge      # merge：合并已标注候选进评测集
    python scripts/sync_feedback_to_dataset.py --dry-run    # 预览不写文件
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models.feedback import Feedback  # noqa: E402

DATASET_PATH = PROJECT_ROOT / "eval_dataset.json"
CANDIDATE_PATH = PROJECT_ROOT / "eval_candidates.json"


def export_candidates(dry_run: bool = False) -> list:
    """拉取 rating=-1 的反馈，生成待人工标注的候选样本（幂等：保留已填写的标注）。"""
    db = SessionLocal()
    try:
        try:
            feedbacks = (
                db.query(Feedback)
                .filter(Feedback.rating == -1)
                .order_by(Feedback.id.desc())
                .all()
            )
        except Exception as e:
            print(f"查询 feedback 表失败（可能未建表，先跑 python scripts/init_db.py）: {e}")
            return []
    finally:
        db.close()

    if not feedbacks:
        print("没有点踩反馈（rating=-1），无需同步。")
        return []

    # 已进评测集的 question（去重：同步过的不再导出）
    dataset_questions = set()
    if DATASET_PATH.exists():
        with open(DATASET_PATH, "r", encoding="utf-8") as f:
            for item in json.load(f):
                dataset_questions.add(item.get("question", "").strip())

    # 候选文件里已有的内容（保留人工已填写的标注，避免重复 export 覆盖）
    existing: dict[str, dict] = {}
    if CANDIDATE_PATH.exists():
        with open(CANDIDATE_PATH, "r", encoding="utf-8") as f:
            for item in json.load(f):
                existing[item.get("question", "").strip()] = item

    candidates = []
    for fb in feedbacks:
        q = (fb.question or "").strip()
        if not q or q in dataset_questions:
            continue  # 空 / 已进评测集
        if q in existing:
            candidates.append(existing[q])  # 保留人工已填的字段
            continue
        candidates.append({
            "feedback_id": fb.id,
            "trace_id": fb.trace_id,
            "question": q,
            "comment": fb.comment or "",
            "status": "pending",
            "expected_intent": "",
            "expected_tools": [],
            "expected_doc_keywords": [],
            "reference_keywords": [],
            "category": "",
        })

    if dry_run:
        print(f"[预览] 共 {len(candidates)} 条候选（待人工标注）：")
        for c in candidates:
            print(f"  #{c['feedback_id']} [{c['status']}] {c['question'][:50]}")
            if c.get("comment"):
                print(f"     用户反馈: {c['comment'][:50]}")
        return candidates

    with open(CANDIDATE_PATH, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    print(f"已生成 {len(candidates)} 条候选 → {CANDIDATE_PATH.name}")
    print("下一步：人工填 expected_intent / expected_tools / reference_keywords / category，")
    print("        把 status 改成 ready，再跑 --merge。")
    return candidates


def merge_candidates(dry_run: bool = False) -> None:
    """把候选文件中已人工标注（status=ready 且 expected_intent 非空）的条目合并进评测集。"""
    if not CANDIDATE_PATH.exists():
        print("候选文件不存在，先跑 export。")
        return

    with open(CANDIDATE_PATH, "r", encoding="utf-8") as f:
        candidates = json.load(f)

    ready = [c for c in candidates if c.get("status") == "ready" and c.get("expected_intent")]
    if not ready:
        print(f"没有 status=ready 且 expected_intent 非空的候选（共 {len(candidates)} 条）。")
        print("人工标注后把 status 改成 ready 再 merge。")
        return

    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    next_id = max((item["id"] for item in dataset), default=0) + 1
    merged_ids = {c["feedback_id"] for c in ready}

    for c in ready:
        dataset.append({
            "id": next_id,
            "question": c["question"],
            "expected_intent": c["expected_intent"],
            "expected_tools": c.get("expected_tools", []),
            "expected_doc_keywords": c.get("expected_doc_keywords", []),
            "reference_keywords": c.get("reference_keywords", []),
            "category": c.get("category") or "feedback",
            "source": f"feedback_{c.get('feedback_id', '')}",
        })
        next_id += 1

    if dry_run:
        print(f"[预览] 将合并 {len(ready)} 条进 eval_dataset.json（现 {len(dataset)} 条）。")
        return

    with open(DATASET_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    print(f"已合并 {len(ready)} 条 → eval_dataset.json（现共 {len(dataset)} 条）")

    remaining = [c for c in candidates if c["feedback_id"] not in merged_ids]
    if remaining:
        with open(CANDIDATE_PATH, "w", encoding="utf-8") as f:
            json.dump(remaining, f, ensure_ascii=False, indent=2)
        print(f"候选文件还剩 {len(remaining)} 条未处理。")
    else:
        CANDIDATE_PATH.unlink(missing_ok=True)
        print("候选文件已清空。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="同步点踩反馈到评测集候选")
    parser.add_argument("--merge", action="store_true",
                        help="合并已标注候选进 eval_dataset.json（默认是 export）")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览不写文件")
    args = parser.parse_args()

    if args.merge:
        merge_candidates(dry_run=args.dry_run)
    else:
        export_candidates(dry_run=args.dry_run)

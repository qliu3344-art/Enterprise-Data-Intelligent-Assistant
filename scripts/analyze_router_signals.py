"""用路由日志里的三个信号（p_top / margin / label_mass）找比单看 p_top 更好的升级判据。

动机：阈值把 p_top 低的那批推去 hybrid，但**错误样本的 p_top 也接近 1**——
改阈值拦不住它们，因为要拦的量和能拦的量不在一处。
但 `p_top` 只是 top1 的概率，它丢掉了两个信息：
  · margin     = top1 − top2，「单选很确定」还是「两个标签贴得很近」
  · label_mass = 三个合法标签的概率和，「模型在三个标签里选」还是「跑到标签外去了」
这两个量都已经在 query_trace 和日志里了，只是没被用上。

**不需要重新调模型**：`logs/app.log` 里每次 intent_route 都记了 q_hash + 三个信号，
按 sha256(question)[:16] 就能和评测报告里的条目对上。所以这是零成本的事后分析。

判据看两件事：
  1. 每个信号在「错/对」两组上的分布差多少（分离度）
  2. 拿它当升级判据时，拦截率 vs 升级率的权衡比单用 p_top 好多少

用法：
    python scripts/analyze_router_signals.py eval_report_20260928_140505.json
"""

import collections
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "app.log")

LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?"
    r"q_hash=(?P<h>[0-9a-f]{16}).*?"
    r"p_top=(?P<p>[0-9.]+) \| label_mass=(?P<m>[0-9.]+) \| "
    r"margin=(?P<g>[0-9.]+) \| latency_ms=(?P<lat>[0-9.]+) \| path=(?P<path>\w+)"
)


def qh(q: str) -> str:
    return hashlib.sha256(q.encode("utf-8")).hexdigest()[:16]


def parse_log():
    """q_hash -> 按时间排好的信号列表（只留走 logprobs 的那些）。"""
    per = collections.defaultdict(list)
    if not os.path.exists(LOG):
        raise SystemExit(f"没有日志文件 {LOG}")
    with open(LOG, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = LINE.search(line)
            if not m or m.group("path") != "logprobs":
                continue
            per[m.group("h")].append(
                (m.group("ts"), float(m.group("p")), float(m.group("m")), float(m.group("g")))
            )
    for v in per.values():
        v.sort()
    return per


def join(report_path):
    per = parse_log()
    report = json.load(open(report_path, encoding="utf-8"))
    rows, miss = [], 0
    # ⚠️ meta.eval_time 是**开始**时间，不是结束时间。取运行窗口内（>= 开始）
    # 的第一条——那才是这次跑出来的信号。写成 <= 会把本次运行整个排除掉。
    start = report.get("meta", {}).get("eval_time", "")[:19].replace("T", " ")
    elapsed = report.get("meta", {}).get("elapsed_seconds") or 0
    for r in report["intent"]:
        cands = [x for x in per.get(qh(r["question"]), []) if x[0] >= start]
        if not cands:
            miss += 1
            continue
        r = dict(r)
        r["p_top"], r["label_mass"], r["margin"] = cands[0][1], cands[0][2], cands[0][3]
        rows.append(r)
    print(f"报告 {os.path.basename(report_path)}：{len(report['intent'])} 条，"
          f"日志对上 {len(rows)} 条，没对上 {miss} 条")
    return rows


def sep(rows, key):
    """分离度：错误组和正确组在这个信号上的中位数差。"""
    e = sorted(r[key] for r in rows if not r.get("correct"))
    c = sorted(r[key] for r in rows if r.get("correct"))
    if not e or not c:
        return None
    med = lambda x: x[len(x) // 2]
    return med(e), med(c), med(c) - med(e)


def sweep(rows, key, descending, thresholds):
    """把「key 越过阈值就升级」当判据，看拦截率/升级率。"""
    errs = [r for r in rows if not r.get("correct")]
    out = []
    for t in thresholds:
        up = [r for r in rows if (r[key] < t if not descending else r[key] >= t)]
        hit = [r for r in up if not r.get("correct")]
        out.append((t, len(up) / len(rows), len(hit) / max(len(errs), 1)))
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "eval_report_20260928_140505.json"
    rows = join(os.path.join(ROOT, path) if not os.path.isabs(path) else path)
    if not rows:
        return
    errs = [r for r in rows if not r.get("correct")]
    print(f"判错 {len(errs)}/{len(rows)}\n")

    print("=== 三个信号在 错/对 两组上的中位数（分离度）===")
    for key, note in [("p_top", "top1 概率——现行判据"),
                      ("margin", "top1−top2"),
                      ("label_mass", "三个合法标签概率和")]:
        s = sep(rows, key)
        print(f"  {key:<12} 错 {s[0]:.3f} | 对 {s[1]:.3f} | 差 {s[2]:+.3f}   {note}")

    print("\n=== 单看 p_top（现行）===")
    print("   阈值   升级率   拦截率")
    for t, u, h in sweep(rows, "p_top", False, [0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 0.95, 0.99]):
        print(f"  {t:>5.2f}  {u:>6.1%}  {h:>6.1%}")

    print("\n=== 单看 margin（越小越该升级）===")
    print("   阈值   升级率   拦截率")
    for t, u, h in sweep(rows, "margin", False, [0.001, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8]):
        print(f"  {t:>5.3f}  {u:>6.1%}  {h:>6.1%}")

    print("\n=== 单看 label_mass（越低越该升级）===")
    print("   阈值   升级率   拦截率")
    for t, u, h in sweep(rows, "label_mass", False, [0.6, 0.8, 0.9, 0.99, 0.999, 1.0]):
        print(f"  {t:>5.3f}  {u:>6.1%}  {h:>6.1%}")


if __name__ == "__main__":
    main()

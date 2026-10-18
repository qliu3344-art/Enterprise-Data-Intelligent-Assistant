"""扫描意图路由阈值：把一份评测报告按不同阈值重切，看期望误判成本落在哪。

这份脚本是**纯后处理**——不调模型、不碰数据库，只吃 `eval_report_*.json` 里
已经落好的 `intent[]`（每条带 confidence 和 correct）。换阈值只是把同一份日志
重新切一刀，所以「再多跑几组对比」在工程上就是这一条命令。

⚠️ 用法是**审计，不是选值**。现行阈值由 692 条实测扫出、写死在
`intent_router.INTENT_CONFIDENCE_THRESHOLD`。这里要回答的不是「最优阈值是多少」——
692 条支撑不起 1 条错答粒度的最优点，扫出来的 argmin 是在噪声上选点。
要回答的是「现行阈值落在一个平坦的区间里吗」：是，则它附近的取值都能用、
系统对参数不挑剔；不是，则说明系统对参数敏感，得回头看选点依据。

    期望误判成本 = 升级率 × C_h + (高代价方向错误数 / 样本数) × C_e

判据看最后一列的碗底平不平：最低点附近 5% 以内有几个阈值连续落在里面。

用法：
    python scripts/scan_threshold.py [eval_report_xxx.json]
    不带参数时取项目根目录下最新的那份报告。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.intent_router import INTENT_CONFIDENCE_THRESHOLD

from report_files import latest_report

# 只有比值进公式，绝对值无意义——成本推导只用 C_h : C_e 这个比值。
C_H = 1
C_E = 4

THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.99]

# —— 「高代价方向错误」的判定 ——
# 不是「data 和 doc 互错」——实测这份报告里 data↔doc 互错是 0 条，19 个错误
# 全部涉及 hybrid：hybrid→data 9、data→hybrid 5、hybrid→doc 3、doc→hybrid 2。
#
# 真正高代价的是**该走双引擎却只给了一路**：用户问的是跨数据与制度的综合问题，
# 判成单引擎就整整少了一路，而且答案读起来是完整的——静默错误。
# 反过来，单引擎问题被升成 hybrid 只是多跑一路、答案更全，是浪费不是错。
#
# ⚠️ 这条规则是**这里定义的**：`eval_dataset_*.json` 只有 category /
# expected_intent / expected_tools，没有这个字段。「高代价方向错误」
# 要能复算就得先固化这条规则，换规则会改那组数——所以别在别处再写一份。
# 实测自洽：这份报告按此规则得 12 条，而 19 个错误里涉及 hybrid 的正好 12 条；
# 400 条口径下是 41/65 = 63%，这里 12/19 = 63%，比例一致。
HIGH_COST_EXPECTED = "hybrid"


def is_high_cost(row):
    """该走 hybrid（双引擎）却判成了单引擎——少一整路，且用户看不出来。"""
    exp, pred = row.get("expected"), row.get("predicted")
    return exp == HIGH_COST_EXPECTED and pred != HIGH_COST_EXPECTED


def load_rows(path=None):
    if not path:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = latest_report(root)
    with open(path, encoding="utf-8") as f:
        report = json.load(f)
    return path, report["intent"]


def main():
    path, rows = load_rows(sys.argv[1] if len(sys.argv) > 1 else None)
    n = len(rows)
    errs = [r for r in rows if not r.get("correct")]
    n_high = sum(1 for r in errs if is_high_cost(r))

    print(f"报告：{os.path.basename(path)}")
    print(f"样本 {n} 条，判错 {len(errs)} 条，其中高代价方向错误 {n_high} 条")
    print(
        f"C_h = {C_H}、C_e = {C_E} → 理论阈值 {1 - C_H / C_E:.2f}"
        f"（现行 {INTENT_CONFIDENCE_THRESHOLD}）\n"
    )

    header = (
        f"{'阈值':>6}{'升级率':>9}{'升级数':>8}{'静默错误':>10}"
        f"{'误判拦截率':>12}{'高代价错':>10}{'期望成本':>10}"
    )
    print(header)
    # 中文占两列，分隔线按显示宽度对齐
    print("-" * (len(header) + sum(1 for ch in header if "一" <= ch <= "鿿")))

    costs = []
    for t in THRESHOLDS:
        up = [r for r in rows if r.get("confidence", 0) < t]
        caught = [r for r in errs if r.get("confidence", 0) < t]
        silent = [r for r in errs if r.get("confidence", 0) >= t]
        high = [r for r in silent if is_high_cost(r)]
        cost = len(up) / n * C_H + len(high) / n * C_E
        costs.append((t, cost))
        mark = "  ← 现行" if abs(t - INTENT_CONFIDENCE_THRESHOLD) < 1e-9 else ""
        print(
            f"{t:>6.2f}{len(up) / n:>8.1%}{len(up):>8}{len(silent):>10}"
            f"{len(caught) / max(len(errs), 1):>11.1%}{len(high):>10}{cost:>10.3f}{mark}"
        )

    best_t, best_c = min(costs, key=lambda x: x[1])
    flat = [t for t, c in costs if c <= best_c * 1.05]
    print(f"\n最低期望成本 {best_c:.3f} @ 阈值 {best_t:.2f}")
    print(f"落在最低值 5% 以内的阈值：{['%.2f' % t for t in flat]}")


if __name__ == "__main__":
    main()

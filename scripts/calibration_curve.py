"""画意图路由置信度的校准曲线：模型说 p 的那批，真实正确率是多少。

`logprobs` 给的是模型的内部概率，比自评置信度接近真值，但**不等于校准过**。
成本推导出来的判据是 `p < 1 - C_h / C_e`，它要求 p 真的是「答对概率」——
成本比估得再准，p 不校准，这个分界就落不到该落的位置上。
成本推导管「在哪分界」，校准管「这个位置可不可信」，两件事。

判读：真实正确率应当贴着平均置信度（对角线）。曲线整体在对角线**下方** =
过自信——模型说 0.75 的那批，实际只对了六成。ECE 是这个偏差按样本量加权的
平均值，越小越好。

ECE 的价值在**对照**：自评版和 logprobs 版各跑一次，两个 ECE 的差就是
「换置信度来源」买到的东西。单看一个数说明不了什么。

⚠️ 样本量决定能分几个桶。117 条按下面这组分箱每桶约 23 条，只够看整体偏不偏；
要正式做温度缩放（拟合一个标量 T）得有千条量级。非空桶数会打印出来，
太少时脚本会自己警告——置信度全挤在一个区间里，本身就是要报告的现象。

用法：
    python scripts/calibration_curve.py [eval_report_xxx.json]
    不带参数时取项目根目录下最新的那份报告。
"""

import json
import os
import sys

from report_files import latest_report

# 上界取 1.01 是为了把 confidence == 1.0 也收进最后一桶（左闭右开）
BINS = [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]


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
    confs = sorted(r.get("confidence", 0) for r in rows)

    # 不同取值的个数是比 ECE 更早暴露问题的指标：置信度如果只有两三个取值，
    # 那它根本没有分辨力，ECE 再低也不能说明校准好——平均偏差小和分得开
    # 是两回事。自评置信度正是这种形态（一堆 0.95 和 0.90）。
    distinct = sorted({r.get("confidence", 0) for r in rows}, reverse=True)

    print(f"报告：{os.path.basename(path)}")
    print(
        f"样本 {n} 条；置信度 min={confs[0]:.3f} 中位={confs[n // 2]:.3f} "
        f"max={confs[-1]:.3f}"
    )
    print(f"不同取值 {len(distinct)} 个：{[round(v, 3) for v in distinct[:8]]}"
          f"{' …' if len(distinct) > 8 else ''}\n")

    header = (
        f"{'置信度区间':>14}{'条数':>7}{'占比':>8}"
        f"{'平均置信度':>12}{'真实正确率':>12}{'偏差':>9}"
    )
    print(header)
    # 中文占两列，分隔线按显示宽度对齐
    print("-" * (len(header) + sum(1 for ch in header if "一" <= ch <= "鿿")))

    ece, nonempty = 0.0, 0
    for lo, hi in zip(BINS, BINS[1:]):
        b = [r for r in rows if lo <= r.get("confidence", 0) < hi]
        label = f"{lo:.1f}~{hi:.2f}"
        if not b:
            print(f"{label:>14}{0:>7}{'—':>8}{'—':>12}{'—':>12}{'—':>9}")
            continue
        nonempty += 1
        conf = sum(r.get("confidence", 0) for r in b) / len(b)
        acc = sum(1 for r in b if r.get("correct")) / len(b)
        ece += len(b) / n * abs(acc - conf)
        print(
            f"{label:>14}{len(b):>7}{len(b) / n:>7.1%}"
            f"{conf:>12.3f}{acc:>11.1%}{acc - conf:>+9.3f}"
        )

    print(f"\nECE = {ece:.3f}    （非空桶 {nonempty}/{len(BINS) - 1}）")
    if nonempty < 3:
        print("⚠️ 非空桶太少，这个 ECE 不可用——置信度全挤在一两个区间里，"
              "说明这个量表分不出层次。这本身就是要报告的现象，"
              "不是脚本的问题。")


if __name__ == "__main__":
    main()

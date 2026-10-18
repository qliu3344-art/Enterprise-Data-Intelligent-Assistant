"""挑「最新的那份评测报告」。

坑：`sorted(glob('eval_report_*.json'))[-1]` 看着像「取最新的」，其实是按**文件名字典序**
排的，而报告目录里混着两类不是时间戳的名字：

  · `eval_report_PROMPT_TEST.json`  —— 'P' 的 ASCII 比数字大，永远排在所有
    `eval_report_20260928_*.json` 之后 → 被当成「最新的一份」。它是当初做 prompt
    对照的小实验（20 条），拿它当默认输入会安静地算出一组没有意义的数。
  · `eval_report_legacy_*.json`      —— 'l' 同理，排在数字开头的之后。

这两类文件本身没错，错的是「靠排序碰运气」这个默认值。所以这里显式按
`eval_report_YYYYMMDD_HHMMSS.json` 的形状匹配，不匹配的一律不进默认列表；
要跑它们就显式传路径。
"""

import glob
import os
import re

# 评测报告的名字形状：eval_report_20260928_210450.json
_REPORT = re.compile(r"^eval_report_(\d{8}_\d{6})\.json$")


def list_reports(root: str) -> list[str]:
    """按时间升序返回带时间戳的评测报告路径（不含 PROMPT_TEST / legacy 等特例）。"""
    stamped = []
    for path in glob.glob(os.path.join(root, "eval_report_*.json")):
        m = _REPORT.match(os.path.basename(path))
        if m:
            stamped.append((m.group(1), path))
    return [path for _, path in sorted(stamped)]


def latest_report(root: str) -> str:
    """最新的那份。没有就报错退出——静默退回某份旧报告比报错更糟。"""
    reports = list_reports(root)
    if not reports:
        raise SystemExit(f"{root} 下没有形如 eval_report_YYYYMMDD_HHMMSS.json 的报告")
    return reports[-1]

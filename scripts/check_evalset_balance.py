"""诊断评测集的构造是否站得住：分母是不是被改写放大了，错误是不是按组聚集。

117 条实际只有 **20 个独立问题**（每组 5~6 个同义改写），所以：
  · 按条目算的错误率会**低估**真实的失败面（同一问题错 6 次只算 1 个认知盲区）
  · 反过来，讲条目的升级率/拦截率时，分母 117 并不代表 117 份独立信息
这个脚本把「条目数」和「独立问题数」两套口径并排打出来，差额就是放大倍数。

⚠️ 顺带查一个会**直接污染错误数**的问题：`category == 'edge_case'` 的条目
`expected_intent` 填的是占位值 `data_query`（三个标签里没有「越界/拒答」这一档），
于是这几条**无论模型答什么都会被判错**。末尾会把它们标出来——
去掉之后才是可评分的准确率。

用法：
    python scripts/check_evalset_balance.py [eval_report_xxx.json]
"""
import collections
import json
import os
import re
import sys

from report_files import latest_report

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ds = json.load(open(os.path.join(root, "eval_dataset_expanded.json"), encoding="utf-8"))
groups = collections.defaultdict(list)
for r in ds:
    m = re.search(r"_(\d+)$", r["source"])
    groups[int(m.group(1))].append(r)

print(f"独立原始问题 {len(groups)} 组  →  展开成 {len(ds)} 条  （放大 {len(ds)/len(groups):.1f}×）")
print()

cat_of = collections.Counter()
for k in sorted(groups):
    cat_of[groups[k][0]["category"]] += 1
print("按独立问题数统计的类别分布：", dict(cat_of))
print("按条目数统计的类别分布  ：", dict(collections.Counter(r["category"] for r in ds)))
print()

print("组  条数  类别          原始问题")
for k in sorted(groups):
    rows = groups[k]
    print(f"{k:>3}{len(rows):>6}  {rows[0]['category']:<12}  {rows[0]['question']}")
print()

# —— 报告侧：错误是否按组聚集 ——
# 默认值走 latest_report()：靠 sorted()[-1] 挑会选中 PROMPT_TEST / legacy 那两份
# （'P'、'l' 的字典序排在数字之后），详见 report_files.py。
target = sys.argv[1] if len(sys.argv) > 1 else latest_report(root)
rep = json.load(open(target, encoding="utf-8"))
print(f"报告：{os.path.basename(target)}")

q2grp = {r["question"]: int(re.search(r"_(\d+)$", r["source"]).group(1)) for r in ds}
size = collections.Counter(q2grp.values())

errs = [r for r in rep["intent"] if not r.get("correct")]
bygrp = collections.defaultdict(list)
for r in errs:
    bygrp[q2grp.get(r.get("question"), -1)].append(r)

n_err, n = len(errs), len(rep["intent"])
print(f"判错 {n_err}/{n} 条，落在 {len(bygrp)} 个独立问题上")
print(f"→ 按条目算的错误率 {n_err/n:.1%}，按独立问题算 {len(bygrp)/len(groups):.1%}")
print()
print("组  该组错/总   期望→预测                              原始问题")
for k in sorted(bygrp):
    rows = bygrp[k]
    q = rows[0].get("question")
    print(
        f"{k:>3}   {len(rows)}/{size.get(k,0):<6} "
        f"{str(rows[0].get('expected'))+' → '+str(rows[0].get('predicted')):<34} {q}"
    )

# —— 不可评分的条目 ——
# expected_intent 是占位值的 edge_case：三标签里没有对应档位，答什么都算错。
edge_grps = {int(re.search(r"_(\d+)$", r["source"]).group(1))
             for r in ds if r["category"] == "edge_case"}
edge_rows = [r for r in rep["intent"] if q2grp.get(r.get("question")) in edge_grps]
n_correct = sum(1 for r in rep["intent"] if r.get("correct"))

print()
print(f"其中 category=edge_case（expected_intent 是占位值 data_query）{len(edge_rows)} 条，"
      f"判错 {sum(1 for r in edge_rows if not r.get('correct'))} 条")
if edge_rows:
    scored = n - len(edge_rows)
    print(f"剔除后：可评分 {scored} 条，正确 {n_correct} → 准确率 {n_correct/scored:.1%}"
          f"（对照：按全量 {n} 条算是 {n_correct/n:.1%}）")
    print("⚠️ 这组不是模型错，是**标签集缺 abstain 档**——标成 data_query 只是占位，"
          "答 data_query 语义上同样不对。要真评它们得先有第四档。")

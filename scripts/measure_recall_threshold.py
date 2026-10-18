"""标定 RECALL_MIN_SCORE：量出「离题查询」与「相关查询」的分数分布，取空隙。

`app/services/memory_store.py` 的召回下限不是拍的，是这里跑出来的。改动语料或
换掉 Embedding 模型之后，这个数要重测——阈值是标定在具体语料分布上的。

标度与 memory_store 完全一致：score = 0.5 + 0.5·cos（`vector_store.similarity_expr`）。
查询侧用 embed_query（带 BGE 前缀）、语料侧用 embed_documents（不带）——与
recall_conventions / save_convention 的真实调用方式一致，不能两边都用一个。

用法：
    python scripts/measure_recall_threshold.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from app.services.rag.embedder import embed_documents, embed_query

# —— 语料：一个真实企业口径库的样子（公司级 + 部门级）——
CORPUS = [
    "加班时长超过3小时算异常",
    "单月加班超过36小时自动标记为考勤异常",
    "年假天数按累计工作年限计算",
    "迟到次数月度超过3次计入异常",
    "请假天数不含法定节假日",
    "出勤天数按实际打卡天数统计，不含远程办公",
    "考勤异常率 = 异常记录数 / 应出勤记录数",
    "合同额指含税合同金额",
    "销售部考核按签约额算，不按回款",
    "销售部的统计口径只含正式员工，不含外包",
    "大促期间销售异常判定基准上浮30%",
    "订单金额统计不含退款订单",
    "销售订单以支付完成时间为准，不以下单时间为准",
    "新客户指首次签约在近90天内的客户",
    "A级客户指年度合同金额不低于10万元",
    "客户等级每年重新评定一次",
    "客户跟进日期按最近一次有效沟通时间记录",
    "流失客户指连续180天无有效跟进的客户",
    "网站访问量按月去重UV统计",
    "运营指标完成率 = 实际值 / 目标值",
    "运营部的目标值在大促月按1.5倍计算",
    "数据质量分满分是1.0，低于0.8视为不合格",
    "异常率 = 异常记录数 / 总记录数",
    "华东区指上海、江苏、浙江、安徽四地",
    "财年从每年4月1日开始",
    "月度数据以自然月为周期，不按财月",
    "金额单位统一为人民币元，外币按当月汇率折算",
    "统计口径以T+1的数据快照为准",
    "部门归属以HR系统当月在职归属为准",
    "历史数据用当期口径重算，不用今天的口径",
    "技术部的考勤不参与异常率统计",
    "客服部工单量按已关闭工单统计",
    "外包人员单独统计，不并入部门人数",
    "试用期员工不计入部门异常率分母",
    "运营部的内容团队并入市场部统计",
    "B级客户指合同金额在5万到10万之间",
    "退款率 = 退款订单数 / 总订单数",
    "复购率按同一客户90天内再次下单计算",
    "销售部周报口径只统计工作日数据",
    "客户联系电话以CRM系统为准，不用订单里的",
]

# —— 相关查询：应当召回上面某一条 ——
ON_TOPIC = [
    "加班多久算异常",
    "这个月加班超过多少小时会被标异常",
    "年假天数怎么算",
    "迟到几次算异常",
    "请假天数里算法定节假日吗",
    "出勤天数怎么统计",
    "考勤异常率是怎么算的",
    "合同额含税吗",
    "销售部考核看签约额还是回款",
    "销售部人数算不算外包",
    "大促期间销售异常判定有什么不同",
    "订单金额算不算退款订单",
    "销售订单按下单时间还是支付时间算",
    "什么算新客户",
    "A级客户的合同金额门槛是多少",
    "客户等级多久重新评一次",
    "客户跟进日期怎么记",
    "什么样的客户算流失",
    "网站访问量按什么口径统计",
    "运营指标完成率怎么算",
    "大促月运营目标值怎么定",
    "数据质量分多少算不合格",
    "整体异常率怎么算",
    "华东区包括哪些地方",
    "财年什么时候开始",
    "月度数据按自然月还是财月",
    "外币金额怎么折算",
    "统计用的数据是什么时点的",
    "部门归属按什么定",
    "去年的数据用哪年的口径算",
    "技术部考勤算进异常率吗",
    "客服部工单量怎么统计",
    "外包人员算不算部门人数",
    "试用期员工算进异常率吗",
    "内容团队算在哪个部门",
    "B级客户的金额区间是多少",
    "退款率怎么算",
    "复购率怎么定义",
    "销售部周报统计周末数据吗",
    "客户联系电话以哪个系统为准",
]

# —— 离题查询：与任何一条口径都无关（含少量「借词」干扰，模拟矮子里拔将军）——
OFF_TOPIC = [
    "今天天气怎么样",
    "帮我订一个明天下午的会议室",
    "你是谁",
    "这个系统用了什么技术栈",
    "推荐一部好看的电影",
    "帮我写一封辞职信",
    "怎么煮一杯手冲咖啡",
    "明天股市会涨吗",
    "上海有什么好吃的小吃",
    "帮我把这段话翻译成英文",
    "讲个笑话",
    "现在几点了",
    "帮我订一张去北京的机票",
    "公司健身房在几楼",
    "围棋怎么入门",
    "推荐几本适合通勤看的书",
    "电脑蓝屏了怎么办",
    "怎么用Excel做数据透视表",
    "周末去哪里露营比较好",
    "教我说几句日语",
    "手机电池不耐用怎么办",
    "王者荣耀怎么上分",
    "北京有哪些值得去的博物馆",
    "怎么养一只猫",
    "空调不制冷是什么原因",
]


def main():
    print("正在向量化语料与查询...")
    # 语料走 embed_documents、查询走 embed_query —— 不是同一条编码路径，
    # 这里复现的是 recall_conventions 与 save_convention 的真实差异。
    conv = np.array(embed_documents(CORPUS))            # (M, 512) 已 L2 归一化
    q_on = np.array([embed_query(q) for q in ON_TOPIC])
    q_off = np.array([embed_query(q) for q in OFF_TOPIC])

    def score_matrix(queries):
        # 已归一化，点积即 cos；再按 similarity_expr 换算到 0.5 + 0.5·cos
        return 0.5 + 0.5 * (queries @ conv.T)

    s_on = score_matrix(q_on)
    s_off = score_matrix(q_off)
    on_top1 = s_on.max(axis=1)
    off_top1 = s_off.max(axis=1)

    print()
    print("=" * 74)
    print("相关查询 — 各自 top1 的分数（这些是下限**不能**砍掉的）")
    print("=" * 74)
    for i in np.argsort(on_top1):
        idx = int(s_on[i].argmax())
        flag = "  ⚠低" if on_top1[i] < 0.70 else ""
        print(f"  {on_top1[i]:.4f}  {ON_TOPIC[i][:26]:<28} ← {CORPUS[idx][:30]}{flag}")

    print()
    print("=" * 74)
    print("离题查询 — 各自 top1 的分数（这些是下限**应该**砍掉的）")
    print("=" * 74)
    for i in np.argsort(-off_top1):
        idx = int(s_off[i].argmax())
        print(f"  {off_top1[i]:.4f}  {OFF_TOPIC[i][:26]:<28} ← {CORPUS[idx][:30]}")

    print()
    print("=" * 74)
    print("结论")
    print("=" * 74)
    print(f"  离题 top1  最高 = {off_top1.max():.4f}   ← 下限必须高于它")
    print(f"  离题 top1  中位 = {np.median(off_top1):.4f}")
    print(f"  相关 top1  最低 = {on_top1.min():.4f}   ← 下限必须低于它")
    print(f"  相关 top1  中位 = {np.median(on_top1):.4f}")
    print(f"  可行空隙 = ({off_top1.max():.4f}, {on_top1.min():.4f})"
          f"   宽度 {on_top1.min() - off_top1.max():.4f}")
    print()

    from app.services.memory_store import RECALL_MIN_SCORE

    print("  候选下限的效果：")
    print(f"  {'下限':>6} {'误杀相关':>10} {'拦住离题':>10}   说明")
    for c in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        lost = int((on_top1 < c).sum())
        blocked = int((off_top1 < c).sum())
        note = "← 现行" if c == RECALL_MIN_SCORE else ""
        print(f"  {c:>6.2f} {lost:>7}/{len(ON_TOPIC):<3} {blocked:>7}/{len(OFF_TOPIC):<3}   {note}")


if __name__ == "__main__":
    main()

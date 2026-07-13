"""生成模拟多源数据文件，用于测试采集和清洗流程。

生成 4 个部门的模拟数据：
  - 考勤：Excel (.xlsx)
  - 销售：CSV (.csv)
  - 客户：Excel (.xlsx)
  - 运营：PDF (通过 fpdf2 生成含表格的 PDF)

用法：
    python scripts/generate_mock_data.py
"""

import os
import sys
import random
from datetime import datetime, timedelta

import pandas as pd

# 确保 data/uploads 目录存在
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 随机种子（可复现）
random.seed(42)

# —— 员工池 ——
EMPLOYEES = [
    ("张三", "EMP001", "技术部"),
    ("李四", "EMP002", "销售部"),
    ("王五", "EMP003", "运营部"),
    ("赵六", "EMP004", "技术部"),
    ("孙七", "EMP005", "销售部"),
    ("周八", "EMP006", "客服部"),
    ("吴九", "EMP007", "运营部"),
    ("郑十", "EMP008", "技术部"),
    ("钱十一", "EMP009", "销售部"),
    ("陈十二", "EMP010", "客服部"),
]


def generate_attendance_excel():
    """考勤数据 — Excel 格式。表头故意用中文变体。"""
    records = []
    start = datetime(2025, 11, 1)

    for i in range(200):
        emp = random.choice(EMPLOYEES)
        date = start + timedelta(days=random.randint(0, 29))
        records.append(
            {
                "员工编号": emp[1],
                "姓名": emp[0],
                "所属部门": emp[2],
                "考勤日期": date.strftime("%Y/%m/%d"),
                "出勤天数": round(random.uniform(18, 22), 1),
                "请假天数": max(0, round(random.gauss(1, 1.5), 1)),
                "加班时长(小时)": max(0, round(random.gauss(10, 8), 1)),
                "迟到次数": max(0, int(random.gauss(1, 2))),
            }
        )

    df = pd.DataFrame(records)
    # 插入一个异常值（加班 80 小时）
    df.loc[199, "加班时长(小时)"] = 80

    path = os.path.join(UPLOAD_DIR, "考勤部-2025.11考勤表.xlsx")
    df.to_excel(path, index=False)
    print(f"✅ 考勤数据: {path} ({len(df)} 条)")


def generate_sales_csv():
    """销售数据 — CSV 格式。使用不同表头命名。"""
    records = []
    start = datetime(2025, 11, 1)

    for i in range(150):
        emp = random.choice(EMPLOYEES)
        date = start + timedelta(days=random.randint(0, 29))
        records.append(
            {
                "工号": emp[1],
                "销售员": emp[0],
                "部门": emp[2],
                "日期": date.strftime("%Y-%m-%d"),
                "订单金额(元)": round(random.uniform(1000, 50000), 2),
                "订单数": random.randint(1, 20),
                "产品": random.choice(["产品A", "产品B", "产品C", "产品D"]),
                "客户": random.choice(["客户X", "客户Y", "客户Z"]),
            }
        )

    # 插入异常值
    records[-1]["订单金额(元)"] = 999999.00

    df = pd.DataFrame(records)
    path = os.path.join(UPLOAD_DIR, "销售部-2025.11销售数据.csv")
    df.to_csv(path, index=False, encoding="utf-8")
    print(f"✅ 销售数据: {path} ({len(df)} 条)")


def generate_customer_excel():
    """客户数据 — Excel 格式。使用英文表头。"""
    levels = ["A", "B", "C", "A", "B", "C", "A", "B"]
    records = []
    start = datetime(2025, 11, 1)

    for i in range(100):
        emp = random.choice(EMPLOYEES)
        date = start + timedelta(days=random.randint(0, 29))
        records.append(
            {
                "EmployeeID": emp[1],
                "Name": emp[0],
                "Dept": emp[2],
                "Date": date.strftime("%Y-%m-%d"),
                "Customer": random.choice(
                    ["客户Alpha", "客户Beta", "客户Gamma", "客户Delta"]
                ),
                "Level": random.choice(levels),
                "ContractAmount": round(random.uniform(5000, 100000), 2),
                "Phone": f"1{random.randint(30, 99)}{random.randint(10000000, 99999999)}",
                "FollowUp": (date + timedelta(days=random.randint(1, 14))).strftime(
                    "%Y-%m-%d"
                ),
            }
        )

    df = pd.DataFrame(records)
    # 留一些缺失值
    df.loc[50:52, "Phone"] = None
    df.loc[80, "ContractAmount"] = None

    path = os.path.join(UPLOAD_DIR, "客户部-2025.11客户数据.xlsx")
    df.to_excel(path, index=False)
    print(f"✅ 客户数据: {path} ({len(df)} 条)")


def generate_operation_pdf():
    """运营数据 — 生成一个包含简单表格的 PDF（纯文本型）。"""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()

    # 尝试使用 Windows 系统中文字体
    font_paths = [
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyh.ttf",
        "C:/Windows/Fonts/simsun.ttf",
    ]
    cjk_font = None
    for fp in font_paths:
        if os.path.exists(fp):
            pdf.add_font("CJK", "", fp, uni=True)
            cjk_font = "CJK"
            break

    if cjk_font:
        pdf.set_font(cjk_font, size=12)
    else:
        pdf.set_font("Helvetica", size=12)

    pdf.cell(200, 10, text="Operations Dept. 2025-11 Monthly Report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(10)

    pdf.set_font(cjk_font, size=10) if cjk_font else pdf.set_font("Helvetica", size=10)
    headers = ["Date", "Metric", "Value", "Unit", "Target", "Rate(%)"]
    col_widths = [35, 50, 30, 25, 30, 25]

    for h, w in zip(headers, col_widths):
        pdf.cell(w, 8, h, border=1, align="C")
    pdf.ln()

    # 数据行（英文以避免字体问题）
    metrics = [
        ("2025-11-01", "Website Visits", "12500", "times", "10000", "125"),
        ("2025-11-07", "New Users", "850", "people", "1000", "85"),
        ("2025-11-14", "Activity Rate", "32.5", "%", "30", "108"),
        ("2025-11-21", "CSAT Score", "4.2", "pts", "4.5", "93"),
        ("2025-11-28", "Revenue", "256000", "CNY", "300000", "85"),
    ]

    for row in metrics:
        for val, w in zip(row, col_widths):
            pdf.cell(w, 8, val, border=1, align="C")
        pdf.ln()

    path = os.path.join(UPLOAD_DIR, "运营部-2025.11月度报表.pdf")
    pdf.output(path)
    print(f"✅ 运营数据: {path} (5 行表格)")


def generate_summary():
    print("\n📊 模拟数据生成完毕！")
    print("  - 考勤数据：200 条（表头：中文变体）")
    print("  - 销售数据：150 条（表头：不同命名）")
    print("  - 客户数据：100 条（表头：英文，含缺失值）")
    print("  - 运营数据：PDF 格式（含 5 行表格）")
    print(f"\n  文件位置: {UPLOAD_DIR}")


if __name__ == "__main__":
    try:
        generate_attendance_excel()
        generate_sales_csv()
        generate_customer_excel()
    except Exception as e:
        print(f"⚠ 部分数据生成失败: {e}")

    # PDF 生成需要 fpdf2
    try:
        generate_operation_pdf()
    except ImportError:
        print("⚠ fpdf2 未安装，跳过 PDF 生成（pip install fpdf2）")
    except Exception as e:
        print(f"⚠ PDF 生成失败: {e}")

    generate_summary()

"""FinSight 智研投 —— Streamlit 入口。

导航：财报分析（阶段4）、买方备忘录（阶段5）、研究报告核验（阶段6）、
投研工作台闭环（阶段7）均已实现。
"""
import streamlit as st

from finsight.ui.bench_page import render_bench_page
from finsight.ui.memo_page import render_memo_page
from finsight.ui.report_page import render_report_page
from finsight.ui.verify_page import render_verify_page

st.set_page_config(page_title="FinSight 智研投", page_icon="📊", layout="wide")

REPORT_PAGE = "财报分析（方向2）"
MEMO_PAGE = "买方备忘录（方向6）"
VERIFY_PAGE = "研报纠错（方向5）"
BENCH_PAGE = "投研工作台（闭环演示）"
PLACEHOLDERS = {}

st.title("FinSight 智研投")
st.caption("上市公司财报分析 · 买方备忘录 · 研报纠错 —— 纯规则版（LLM 通道预留）")

with st.sidebar:
    choice = st.radio("功能导航", [REPORT_PAGE, MEMO_PAGE, VERIFY_PAGE, BENCH_PAGE])

st.divider()
if choice == REPORT_PAGE:
    render_report_page()
elif choice == MEMO_PAGE:
    render_memo_page()
elif choice == VERIFY_PAGE:
    render_verify_page()
else:
    render_bench_page()

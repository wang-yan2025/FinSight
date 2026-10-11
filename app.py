"""FinSight 智研投 —— Streamlit 入口。

导航：财报分析页（阶段4）、买方备忘录页（阶段5）、研究报告核验页（阶段6）均已实现。
"""
import streamlit as st

from finsight.ui.memo_page import render_memo_page
from finsight.ui.report_page import render_report_page
from finsight.ui.verify_page import render_verify_page

st.set_page_config(page_title="FinSight 智研投", page_icon="📊", layout="wide")

REPORT_PAGE = "财报分析（方向2）"
MEMO_PAGE = "买方备忘录（方向6）"
VERIFY_PAGE = "研报纠错（方向5）"
PLACEHOLDERS = {}

st.title("FinSight 智研投")
st.caption("上市公司财报分析 · 买方备忘录 · 研报纠错 —— 纯规则版（LLM 通道预留）")

with st.sidebar:
    choice = st.radio("功能导航", [REPORT_PAGE, MEMO_PAGE, VERIFY_PAGE])

st.divider()
if choice == REPORT_PAGE:
    render_report_page()
elif choice == MEMO_PAGE:
    render_memo_page()
else:
    render_verify_page()

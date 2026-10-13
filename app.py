"""FinSight 智研投 —— Streamlit 入口。

导航：按钮式侧边栏（session_state 管理当前页，无原生 radio 痕迹）。
页面函数入口与各页面 session_state 隔离逻辑保持不变。
"""
import os

import streamlit as st

import finsight as _fs
from finsight.config import load_env_file
from finsight.ui import styles
from finsight.ui.bench_page import render_bench_page
from finsight.ui.memo_page import render_memo_page
from finsight.ui.report_page import render_report_page
from finsight.ui.verify_page import render_verify_page

load_env_file()  # .env（gitignored）中的 LLM 配置优先级低于系统环境变量

# 补充读取 Streamlit Secrets（本地 .streamlit/secrets.toml / 云端平台 Secrets）
# 仅填充 .env 未覆盖的变量；测试模式（FINSIGHT_IGNORE_ENV_FILE=1）跳过
if os.environ.get("FINSIGHT_IGNORE_ENV_FILE") != "1":
    try:
        for key in ("FINSIGHT_LLM_API_KEY", "FINSIGHT_LLM_BASE_URL", "FINSIGHT_LLM_MODEL"):
            if key not in os.environ and key in st.secrets:
                os.environ[key] = st.secrets[key]
    except Exception:
        pass  # 非 Streamlit 环境或无 secrets 文件时忽略

st.set_page_config(page_title="FinSight 智研投", page_icon="📊", layout="wide")
styles.inject()

REPORT_PAGE = "财务分析中心"
MEMO_PAGE = "投资备忘录"
VERIFY_PAGE = "独立报告核验"
BENCH_PAGE = "智能投研工作台"
NAV = [
    (REPORT_PAGE, "📊"),
    (MEMO_PAGE, "📝"),
    (VERIFY_PAGE, "🛡️"),
    (BENCH_PAGE, "🔄"),
]
_PAGE_KEY = "fs_page"

# ---- 侧边栏：品牌区 + 按钮导航 + 底部信息 ----
with st.sidebar:
    st.markdown(
        """<div class="fs-brand"><div class="fs-logo">FS</div>
        <div><div class="fs-brand-title">FinSight 智研投</div>
        <div class="fs-brand-sub">Financial Intelligence Platform</div></div></div>""",
        unsafe_allow_html=True)
    current = st.session_state.get(_PAGE_KEY, REPORT_PAGE)
    for name, icon in NAV:
        if st.button(f"{icon}  {name}", key=f"nav_{name}",
                     type="primary" if name == current else "secondary",
                     use_container_width=True):
            st.session_state[_PAGE_KEY] = name
            st.rerun()
    _mode = "离线守卫 ON" if os.environ.get("FINSIGHT_OFFLINE") == "1" else "在线模式"
    st.markdown(
        '<div class="fs-side-foot">'
        f'<div class="row"><span class="k">数据</span>东方财富 · akshare</div>'
        f'<div class="row"><span class="k">引擎</span>v{_fs.__version__} · {_mode}</div>'
        '<div class="row"><span class="k">声明</span>风险输出均为 risk_hint</div>'
        '</div>', unsafe_allow_html=True)

st.divider()
choice = st.session_state.get(_PAGE_KEY, REPORT_PAGE)
if choice == REPORT_PAGE:
    render_report_page()
elif choice == MEMO_PAGE:
    render_memo_page()
elif choice == VERIFY_PAGE:
    render_verify_page()
else:
    render_bench_page()

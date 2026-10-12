"""FinSight 全局视觉样式（Premium Fintech Dashboard）。

集中管理全部自定义 CSS；页面不散落样式字符串。
- 只使用稳定容器（stSidebar/stApp/stButton/stStatus）与自定义 class；
- 无外部字体/CDN/JS；含 prefers-reduced-motion 降级。
"""
from __future__ import annotations

# 设计令牌
C = {
    "navy": "#101E35", "navy2": "#0D1A2F", "blue": "#376CFB", "teal": "#15B8A6",
    "bg": "#F5F7FB", "card": "#FFFFFF", "ink": "#1D2939", "muted": "#667085",
    "line": "#E4E9F2", "gold": "#B7791F", "goldbg": "#FFF4E0",
    "red": "#C0392B", "redbg": "#FDECEC", "tealbg": "#E6F7F4", "tealdark": "#0E8C7E",
    "bluebg": "#EAF0FE", "neutralbg": "#EEF2F8", "neutralink": "#51607A",
}


def global_css() -> str:
    return f"""
<style>
:root {{
  --fs-blue:{C['blue']}; --fs-teal:{C['teal']}; --fs-navy:{C['navy']};
  --fs-ink:{C['ink']}; --fs-muted:{C['muted']}; --fs-line:{C['line']};
}}
/* ---------- 全局基调 ---------- */
#MainMenu, footer {{visibility:hidden;}}
.stApp {{ background:{C['bg']}; }}
h1,h2,h3 {{ color:{C['ink']}; letter-spacing:.2px; }}
.block-container {{ padding-top:1.6rem; padding-bottom:2.5rem; max-width:1440px;
  padding-left:2.2rem; padding-right:2.2rem; }}

/* ---------- 顶部工具栏（透明化，消除品牌遮挡） ---------- */
header[data-testid="stHeader"] {{ background:transparent; }}
header[data-testid="stHeader"] .stDeployButton {{ display:none; }}
[data-testid="stSidebarCollapsedControl"] {{ color:#98A8B8; }}
[data-testid="stSidebarCollapsedControl"] svg {{ fill:#98A8B8; }}

/* ---------- 侧边栏（深海军蓝品牌导航） ---------- */
[data-testid="stSidebar"] {{
  background: linear-gradient(180deg,{C['navy2']} 0%,{C['navy']} 100%);
  border-right:1px solid rgba(255,255,255,.06);
}}
[data-testid="stSidebar"] * {{ color:#D7DEEA; }}
[data-testid="stSidebar"] .fs-brand-title {{ color:#fff; font-weight:700; font-size:1.16rem;
  line-height:1.3; white-space:nowrap; }}
[data-testid="stSidebar"] .fs-brand-sub {{ color:#8FA0BC; font-size:.7rem; letter-spacing:.15em;
  text-transform:uppercase; margin-top:3px; line-height:1.35; white-space:nowrap; }}
.fs-brand {{ display:flex; gap:12px; align-items:center; padding:8px 4px 16px; margin-top:14px; }}
.fs-logo {{ width:42px; height:42px; border-radius:12px; flex:0 0 42px;
  background:linear-gradient(135deg,{C['blue']},{C['teal']});
  display:flex; align-items:center; justify-content:center;
  color:#fff; font-weight:800; font-size:15px; letter-spacing:.5px;
  box-shadow:0 4px 14px rgba(55,108,251,.35); }}

/* 按钮式导航项（无原生控件痕迹；用宽泛选择器保证兼容） */
section[data-testid="stSidebar"] button {{
  display:flex; align-items:center; gap:10px; text-align:left !important;
  background:transparent !important; border:none !important; box-shadow:none !important;
  color:#C3CEDF !important; font-size:.93rem; font-weight:500;
  padding:11px 14px !important; border-radius:11px; min-height:44px;
  width:100%; transition:background .18s ease, color .18s ease; }}
section[data-testid="stSidebar"] button:hover {{
  background:rgba(255,255,255,.08) !important; color:#fff !important; }}
section[data-testid="stSidebar"] button[kind="primary"] {{
  background:linear-gradient(90deg,rgba(55,108,251,.35),rgba(55,108,251,.22)) !important;
  color:#fff !important; font-weight:650;
  box-shadow:inset 3px 0 0 0 {C['teal']}; }}
section[data-testid="stSidebar"] button[kind="primary"]:hover {{
  background:linear-gradient(90deg,rgba(55,108,251,.45),rgba(55,108,251,.30)) !important; }}
section[data-testid="stSidebar"] button p {{ font-size:inherit; color:inherit; }}

.fs-side-foot {{ margin-top:16px; padding:12px 14px 4px;
  border-top:1px solid rgba(255,255,255,.10); }}
.fs-side-foot .row {{ display:flex; gap:8px; align-items:baseline; margin-bottom:7px;
  font-size:.73rem; color:#8FA0BC; }}
.fs-side-foot .k {{ flex:0 0 34px; color:#5F7292; letter-spacing:.08em; }}

/* ---------- 页头横幅 ---------- */
.fs-hero {{ background:linear-gradient(120deg,{C['navy']} 0%,#1B3B6F 68%,{C['blue']} 130%);
  border-radius:16px; padding:20px 26px; color:#fff; margin-bottom:6px;
  display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap;
  animation:fsFadeUp .32s ease both; }}
.fs-hero h1 {{ color:#fff; font-size:1.45rem; margin:0; font-weight:700; }}
.fs-hero .fs-hero-sub {{ color:#B9C6DE; font-size:.86rem; margin-top:4px; }}
.fs-hero .fs-chiprow {{ display:flex; gap:8px; flex-wrap:wrap; }}
.fs-chip {{ background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.16);
  color:#EAF0FA; border-radius:999px; padding:4px 12px; font-size:.76rem; white-space:nowrap; }}
.fs-chip b {{ color:#fff; }}

/* ---------- 卡片 ---------- */
div[data-testid="stVerticalBlock"] > .fs-card-slot .stVerticalBlock,
.fs-card {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:14px; }}
.fs-card-pad {{ padding:16px 18px; }}
.fs-card-hover {{ transition:transform .18s ease, box-shadow .18s ease; }}
.fs-card-hover:hover {{ transform:translateY(-3px); box-shadow:0 10px 24px rgba(16,30,53,.10); }}
.fs-lift {{ animation:fsFadeUp .32s ease both; }}
@keyframes fsFadeUp {{ from {{opacity:0; transform:translateY(10px);}} to {{opacity:1; transform:none;}} }}
.fs-sec-title {{ font-size:1.02rem; font-weight:700; color:{C['ink']};
  display:flex; align-items:center; gap:8px; margin:2px 0 10px; }}
.fs-sec-title::before {{ content:""; width:4px; height:16px; border-radius:2px;
  background:linear-gradient(180deg,{C['blue']},{C['teal']}); }}

/* ---------- KPI 卡 ---------- */
.fs-kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
  gap:12px; margin:4px 0 6px; }}
.fs-kpi {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:13px;
  padding:13px 15px; animation:fsFadeUp .3s ease both;
  transition:transform .18s ease, box-shadow .18s ease; }}
.fs-kpi:hover {{ transform:translateY(-3px); box-shadow:0 10px 22px rgba(16,30,53,.10); }}
.fs-kpi .k-label {{ font-size:.76rem; color:{C['muted']}; display:flex; justify-content:space-between;
  align-items:center; gap:4px; overflow:hidden; white-space:nowrap; }}
.fs-kpi .k-value {{ font-size:1.28rem; font-weight:750; color:{C['ink']};
  font-variant-numeric:tabular-nums; margin-top:3px; white-space:nowrap; }}
.fs-kpi .k-value small {{ font-size:.78rem; color:{C['muted']}; font-weight:600; margin-left:2px; }}
.fs-kpi .k-sub {{ font-size:.72rem; color:{C['muted']}; margin-top:3px;
  font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }}

/* ---------- 状态徽标 ---------- */
.fs-b {{ display:inline-block; border-radius:999px; padding:2px 10px;
  font-size:.72rem; font-weight:600; line-height:1.6; white-space:nowrap; }}
.fs-b-pass {{ background:{C['tealbg']}; color:{C['tealdark']}; }}
.fs-b-fail {{ background:{C['redbg']}; color:{C['red']}; }}
.fs-b-warn {{ background:{C['goldbg']}; color:{C['gold']}; }}
.fs-b-info {{ background:{C['bluebg']}; color:{C['blue']}; }}
.fs-b-neutral {{ background:{C['neutralbg']}; color:{C['neutralink']}; }}

/* ---------- 核验状态分布条 ---------- */
.fs-dist {{ display:flex; height:12px; border-radius:8px; overflow:hidden;
  background:{C['neutralbg']}; margin:8px 0 6px; }}
.fs-dist i {{ display:block; height:100%; transition:width .5s ease; }}
.fs-legend {{ display:flex; gap:14px; flex-wrap:wrap; font-size:.74rem; color:{C['muted']}; }}
.fs-legend s {{ text-decoration:none; display:inline-block; width:9px; height:9px;
  border-radius:3px; margin-right:5px; }}

/* ---------- 工作流步骤条 ---------- */
.fs-steps {{ display:flex; align-items:flex-start; gap:6px; margin:6px 0 10px; flex-wrap:wrap; }}
.fs-step {{ flex:1 1 150px; min-width:150px; text-align:center; position:relative; }}
.fs-step .dot {{ width:34px; height:34px; border-radius:50%; margin:0 auto 6px;
  display:flex; align-items:center; justify-content:center; font-size:.8rem; font-weight:700;
  background:{C['neutralbg']}; color:{C['neutralink']}; border:2px solid transparent;
  transition:all .25s ease; }}
.fs-step .lbl {{ font-size:.78rem; color:{C['muted']}; font-weight:600; }}
.fs-step .bar {{ height:4px; border-radius:2px; background:{C['neutralbg']}; margin-top:7px;
  transition:background .25s ease; }}
.fs-step.done .dot {{ background:{C['tealbg']}; color:{C['tealdark']}; border-color:{C['teal']}; }}
.fs-step.done .bar {{ background:{C['teal']}; }}
.fs-step.active .dot {{ background:{C['blue']}; color:#fff;
  box-shadow:0 0 0 5px rgba(55,108,251,.18); }}
.fs-step.active .lbl {{ color:{C['blue']}; }}
.fs-step.active .bar {{ background:linear-gradient(90deg,{C['blue']},{C['teal']}); }}
.fs-step.error .dot {{ background:{C['redbg']}; color:{C['red']}; border-color:{C['red']}; }}
.fs-step.error .lbl {{ color:{C['red']}; }}

/* ---------- 修订前后对比卡 ---------- */
.fs-diff {{ display:grid; grid-template-columns:1fr 1.05fr 1fr; gap:10px; margin-top:4px; }}
@media (max-width:980px) {{ .fs-diff {{ grid-template-columns:1fr; }} }}
.fs-diff .col {{ border-radius:12px; padding:12px 14px; border:1px solid {C['line']};
  background:{C['card']}; font-size:.82rem; line-height:1.65; }}
.fs-diff .mid {{ background:{C['bluebg']}; border-color:#CBDAFD; }}
.fs-diff h5 {{ margin:0 0 6px; font-size:.76rem; letter-spacing:.06em; color:{C['muted']};
  text-transform:uppercase; }}
.fs-quote {{ background:{C['neutralbg']}; border-left:3px solid {C['neutralink']};
  border-radius:6px; padding:7px 10px; color:{C['ink']}; }}
.fs-quote.ok {{ border-left-color:{C['teal']}; background:{C['tealbg']}; }}

/* ---------- 全局控件精修（去默认痕迹） ---------- */
/* 主操作按钮 */
.stButton > button[kind="primary"] {{
  background:linear-gradient(135deg,{C['blue']},#2F5FE8); color:#fff;
  border:none; border-radius:10px; padding:.45rem 1.15rem; font-weight:600;
  letter-spacing:.04em; box-shadow:0 4px 12px rgba(55,108,251,.28);
  transition:transform .15s ease, box-shadow .15s ease; }}
.stButton > button[kind="primary"]:hover {{
  transform:translateY(-1px); box-shadow:0 6px 16px rgba(55,108,251,.36); }}
/* 次级按钮（主内容区；侧边栏导航已单独覆盖） */
section.main .stButton > button[kind="secondary"], .block-container .stButton > button[kind="secondary"] {{
  background:{C['card']}; color:{C['ink']}; border:1px solid {C['line']};
  border-radius:10px; padding:.42rem 1.05rem; font-weight:550;
  transition:border-color .15s ease, color .15s ease; }}
.block-container .stButton > button[kind="secondary"]:hover {{
  border-color:{C['blue']}; color:{C['blue']}; }}
/* 输入 / 选择框 */
.stTextInput input, div[data-baseweb="input"] > input {{
  border-radius:10px; border-color:{C['line']}; background:{C['card']}; }}
.stTextInput:focus-within, div[data-baseweb="input"]:focus-within {{
  border-color:{C['blue']} !important; box-shadow:0 0 0 3px rgba(55,108,251,.14); border-radius:10px; }}
div[data-baseweb="select"] > div {{ border-radius:10px; border-color:{C['line']};
  background:{C['card']}; font-size:.92rem; }}
div[data-baseweb="select"] > div:hover {{ border-color:#C6D2E8; }}
/* 提示框（弱化默认 alert 边框感） */
div[data-testid="stAlert"] {{ border-radius:12px; border:1px solid; font-size:.9rem;
  border-left-width:3px; }}
div[data-testid="stAlert"][kind="info"] {{ background:#F0F5FF; border-color:#D4E0FA; }}
div[data-testid="stAlert"][kind="success"] {{ background:#EDF9F6; border-color:#C9EDE7; }}
div[data-testid="stAlert"][kind="warning"] {{ background:#FFF9EE; border-color:#F3E2BD; }}
div[data-testid="stAlert"][kind="error"] {{ background:#FDF1F0; border-color:#F2CFCC; }}
/* 指标卡 */
div[data-testid="stMetric"] {{ background:{C['card']}; border:1px solid {C['line']};
  border-radius:12px; padding:12px 14px; }}
div[data-testid="stMetric"] label {{ font-size:.76rem; color:{C['muted']}; }}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {{
  font-variant-numeric:tabular-nums; font-weight:700; }}
/* Tab（下划线式） */
.stTabs [data-baseweb="tab-list"] {{ gap:4px; border-bottom:1px solid {C['line']}; }}
.stTabs [data-baseweb="tab"] {{ border-radius:9px 9px 0 0; padding:7px 16px;
  font-weight:600; color:{C['muted']}; font-size:.9rem; }}
.stTabs [data-baseweb="tab"]:hover {{ color:{C['ink']}; background:{C['neutralbg']}; }}
.stTabs [data-baseweb="tab"][aria-selected="true"] {{ background:{C['card']};
  color:{C['blue']}; box-shadow:inset 0 -2px 0 {C['blue']}; }}
/* Expander 标题 */
[data-testid="stExpander"] summary p {{ font-weight:600; }}
/* 状态标签（LLM 等裸文本替代） */
.fs-chip-line {{ display:flex; gap:8px; align-items:center; margin:2px 0 8px; flex-wrap:wrap; }}
.fs-chip-line .cap {{ font-size:.78rem; color:{C['muted']}; }}

/* ---------- 其他 ---------- */
.fs-eid {{ font-family:ui-monospace,Consolas,monospace; font-size:.74em;
  background:{C['neutralbg']}; color:{C['neutralink']}; border-radius:5px; padding:1px 5px; }}
.fs-srcnote {{ font-size:.74rem; color:{C['muted']}; }}
[data-testid="stDataFrame"] {{ border:1px solid {C['line']}; border-radius:12px; overflow:hidden; }}
[data-testid="stExpander"] {{ border:1px solid {C['line']}; border-radius:12px; }}
/* 尊重减弱动态偏好 */
@media (prefers-reduced-motion: reduce) {{
  * {{ animation:none !important; transition:none !important; }}
}}
</style>
"""


def inject() -> None:
    import streamlit as st
    st.markdown(global_css(), unsafe_allow_html=True)

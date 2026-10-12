"""正式工作台执行日志测试：run_id 串联、重渲染不重复、脱敏、独立分发 B/C。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from finsight.datasources import em_statements
from finsight.finance.field_mapping import REPORT_DATE_FIELD

APP = Path(__file__).resolve().parent.parent / "app.py"
BENCH_PAGE = "智能投研工作台"

DATES = [f"{y}-{m:02d}-{d:02d} 00:00:00" for y in (2023, 2024)
         for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
_cum = lambda xs: list(__import__("itertools").accumulate(xs))
E8 = lambda lst: [v * 1e8 for v in lst]
RAW_BY_KIND = {
    "income": pd.DataFrame({
        REPORT_DATE_FIELD: DATES,
        "OPERATE_INCOME": E8(_cum([100] * 8)),
        "NETPROFIT": E8(_cum([50] * 8)),
    }),
}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("FINSIGHT_IGNORE_ENV_FILE", "1")
    for var in ("FINSIGHT_LLM_API_KEY", "FINSIGHT_LLM_BASE_URL", "FINSIGHT_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FINSIGHT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(em_statements, "fetch_statement",
                        lambda code, kind, refresh=False: RAW_BY_KIND[kind].copy())
    return tmp_path / "logs"


def _drive(at):
    at.text_input(key="fs_bench_code").set_value("600519")
    at.button(key="fs_bench_load").click().run()
    at.button(key="fs_bench_run1").click().run()
    return at


def test_bench_creates_run_and_threads_run_id(_env):
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.button(key=f"nav_{BENCH_PAGE}").click()
    at.run()
    at = _drive(at)

    logs = list((_env).glob("run_*.jsonl"))
    assert len(logs) == 1, "正式工作台应创建一个活动 run 日志文件"
    events = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
    run_ids = {e["run_id"] for e in events}
    assert len(run_ids) == 1 and events[0]["step"] == "run_start"
    steps = [e["step"] for e in events]
    for required in ("memo_generated", "verification_round", "proposals_generated",
                     "export_memo", "export_report", "evidence_field",
                     "risk_rules_executed"):
        assert required in steps, required
    assert events[0]["company"] == "SH600519"


def test_bench_rerun_does_not_duplicate_logs(_env):
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.button(key=f"nav_{BENCH_PAGE}").click()
    at.run()
    at = _drive(at)
    log = next(_env.glob("run_*.jsonl"))
    count_before = len(log.read_text(encoding="utf-8").splitlines())
    # 多次重渲染（无按钮点击）
    for _ in range(3):
        at.run()
    count_after = len(log.read_text(encoding="utf-8").splitlines())
    assert count_after == count_before, "页面重渲染不得产生重复日志事件"


def test_bench_full_loop_same_run_and_no_secrets(_env):
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.button(key=f"nav_{BENCH_PAGE}").click()
    at.run()
    at = _drive(at)
    session = at.session_state["fs_bench_session"]
    det = [p for p in session.proposals if p.deterministic]
    if det:
        for p in det:
            at.checkbox(key=f"fs_bench_pick_{p.pid}").check()
        at.run()
        at.button(key="fs_bench_revise").click().run()
    log = next(_env.glob("run_*.jsonl"))
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    steps = [e["step"] for e in events]
    if det:
        assert "revision_confirmed" in steps and "revision_applied" in steps
        assert steps.count("verification_round") >= 2      # 两轮核验同一 run
    body = log.read_text(encoding="utf-8")
    assert "sk-" not in body and "Bearer" not in body       # 无密钥样式
    assert "api_key" not in body
    # 不落报告正文（事件仅含计数/哈希/数值字段）
    assert "研究辅助稿" not in body and "免责声明" not in body


def test_bench_context_switch_finalizes_old_run(_env):
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.button(key=f"nav_{BENCH_PAGE}").click()
    at.run()
    at = _drive(at)
    # 切换标的重新加载 → 旧 run 应 finish(aborted)，新 run 独立
    at.text_input(key="fs_bench_code").set_value("000001")
    at.button(key="fs_bench_load").click().run()
    at.button(key="fs_bench_run1").click().run()
    # 同秒内创建的文件名排序含随机 uuid：按修改时间排序保证先后顺序
    logs = sorted(_env.glob("run_*.jsonl"), key=lambda p: p.stat().st_mtime)
    assert len(logs) == 2
    first = [json.loads(x) for x in logs[0].read_text(encoding="utf-8").splitlines()]
    finishes = [e for e in first if e["step"] == "run_finish"]
    assert finishes and finishes[-1]["status"] == "aborted"
    second = [json.loads(x) for x in logs[1].read_text(encoding="utf-8").splitlines()]
    assert {e["company"] for e in second} == {"SZ000001"}


def test_routes_b_c_standalone_without_fixtures(tmp_path):
    """B/C 路线在删除真实数据切片的副本上仍可独立运行（可分发方案）。"""
    work = tmp_path / "repo"
    shutil.copytree(Path(__file__).resolve().parent.parent, work,
                    ignore=shutil.ignore_patterns("data", ".git", "__pycache__",
                                                  ".pytest_cache", ".mypy_cache"))
    for f in (work / "tests" / "fixtures").glob("SH600519_*.csv"):
        f.unlink()          # 模拟交付包不含真实数据切片
    result = subprocess.run(
        [sys.executable, str(work / "scripts" / "replay_demo.py"), "--route", "B"],
        capture_output=True, text=True, cwd=work, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "路线 B" in result.stdout and "缺失——路线 A 不可用" in result.stdout

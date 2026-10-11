"""交付能力测试：执行日志、离线守卫、样本哈希、确定性与隔离。"""
import json
from pathlib import Path

import pandas as pd
import pytest

from finsight.audit import RunLogger, _scrub, start_run
from finsight.datasources import em_statements
from finsight.finance.field_mapping import REPORT_DATE_FIELD, standardize_statement
from finsight.workflow.pipeline import start_session


def _frames():
    def cum(xs):
        t, o = 0, []
        for x in xs:
            t += x
            o.append(t)
        return o
    e8 = lambda lst: [v * 1e8 for v in lst]
    dates = [f"{y}-{m:02d}-{d:02d} 00:00:00" for y in (2023, 2024)
             for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    inc = standardize_statement("income", pd.DataFrame({
        REPORT_DATE_FIELD: dates,
        "OPERATE_INCOME": e8(cum([100] * 4) + cum([120, 110, 90, 95])),
        "NETPROFIT": e8(cum([50] * 4) + cum([10, -5, -10, -15])),
    }))
    return inc


# ---------------- 日志基础 ----------------

def test_scrub_removes_secrets():
    data = {"api_key": "sk-abcdefgh12345678", "note": "token=xyzsecretvalue99",
            "list": ["Bearer abcdefghijk"], "plain": "正常文本 415"}
    out = _scrub(data)
    assert out["api_key"] == "«REDACTED»"
    assert "xyzsecretvalue99" not in json.dumps(out, ensure_ascii=False)
    assert "Bearer" not in out["list"][0]
    assert out["plain"] == "正常文本 415"


def test_run_logger_records_events_with_run_id(tmp_path):
    with start_run(company="T", period="2024Q4", log_dir=tmp_path,
                   params={"route": "unit"}) as run:
        from finsight.audit import audit_event, audit_fail
        audit_event("data_cache_read", kind="income")
        try:
            raise ValueError("示例错误")
        except ValueError as exc:
            audit_fail("data_fetch_failed", exc, kind="income")
    events = run.events()
    assert events[0]["step"] == "run_start" and events[0]["params"] == {"route": "unit"}
    ids = {e["run_id"] for e in events}
    assert ids == {run.run_id}                      # 全部事件同一 run_id
    steps = [e["step"] for e in events]
    assert "data_cache_read" in steps and "run_finish" in steps
    err = next(e for e in events if e["step"] == "data_fetch_failed")
    assert err["status"] == "error" and "示例错误" in err["error"]
    # 脱敏：密钥样式不落日志
    with start_run(company="T", period="", log_dir=tmp_path) as run2:
        from finsight.audit import audit_event
        audit_event("sensitive", api_key="sk-verysecretkey123", text="Bearer abcdef12345")
    body = run2.path.read_text(encoding="utf-8")
    assert "sk-verysecretkey123" not in body and "abcdef12345" not in body


def test_noop_without_active_run():
    from finsight.audit import audit_event, current_run_id
    assert current_run_id() is None
    audit_event("should_not_write")          # 不抛错、不写文件
    assert current_run_id() is None


# ---------------- run_id 串联关键步骤 ----------------

def test_run_id_threads_pipeline_and_exports(tmp_path):
    from finsight.memo.render import memo_to_markdown
    from finsight.verify.report import report_to_markdown
    with start_run(company="T", period="2024Q4", log_dir=tmp_path) as run:
        inc = _frames()
        session = start_session("T", inc, None, None)
        memo_to_markdown(session.versions["V1"].memo)
        report_to_markdown(session.rounds[1].report)
    events = run.events()
    steps = {e["step"] for e in events}
    for required in ("run_start", "evidence_field", "risk_rules_executed",
                     "memo_generated", "verification_round", "proposals_generated",
                     "export_memo", "export_report", "run_finish"):
        assert required in steps, required
    ver = next(e for e in events if e["step"] == "verification_round")
    assert ver["snapshot_fingerprint"] == session.fingerprint
    assert ver["counts"]["pass"] >= 1


# ---------------- 离线守卫 ----------------

def test_offline_guard_blocks_network(tmp_path, monkeypatch):
    monkeypatch.setenv("FINSIGHT_OFFLINE", "1")
    monkeypatch.setattr(em_statements, "CACHE_DIR", tmp_path)  # 无缓存
    called = {"fetcher": False}

    def fake_fetcher(symbol):
        called["fetcher"] = True
        return pd.DataFrame({"x": [1]})

    monkeypatch.setattr(em_statements, "_akshare_fetchers",
                        lambda: {"income": fake_fetcher})
    with pytest.raises(RuntimeError, match="离线模式"):
        em_statements.fetch_statement("600519", "income")
    assert called["fetcher"] is False           # 守卫先于任何接口尝试


def test_offline_guard_allows_fresh_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FINSIGHT_OFFLINE", "1")
    monkeypatch.setattr(em_statements, "CACHE_DIR", tmp_path)
    (tmp_path / "SH600519_income.csv").write_text(
        "REPORT_DATE\n2024-06-30 00:00:00\n", encoding="utf-8-sig")
    df = em_statements.fetch_statement("600519", "income")   # 命中缓存，不触发守卫
    assert "REPORT_DATE" in df.columns


# ---------------- 样本哈希守卫 ----------------

def test_manifest_hash_mismatch_rejected(tmp_path, monkeypatch):
    """样本被篡改时 replay 必须拒绝执行（通过子进程验证，真实退出码）。"""
    import subprocess, sys, shutil, hashlib, json as jsonlib
    work = tmp_path / "repo"
    shutil.copytree(Path(__file__).resolve().parent.parent, work,
                    ignore=shutil.ignore_patterns("data", ".git", "__pycache__",
                                                  ".pytest_cache", ".mypy_cache"))
    target = work / "tests" / "fixtures" / "SH600519_income_sample.csv"
    target.write_text(target.read_text(encoding="utf-8-sig") + "\n", encoding="utf-8-sig")
    result = subprocess.run([sys.executable, str(work / "scripts" / "replay_demo.py"),
                             "--route", "B"], capture_output=True, text=True,
                            cwd=work, timeout=120)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "哈希不匹配" in result.stdout


def test_manifest_hashes_match_repo():
    manifest = json.loads((Path(__file__).parent / "fixtures" / "MANIFEST.json")
                          .read_text(encoding="utf-8"))
    import hashlib
    for name, meta in manifest["fixtures"].items():
        digest = hashlib.sha256(
            (Path(__file__).parent / "fixtures" / name).read_bytes()).hexdigest()
        assert digest == meta["sha256"], name


# ---------------- 确定性与隔离 ----------------

def test_same_input_same_verification_result():
    inc = _frames()
    s1 = start_session("T", inc, None, None)
    s2 = start_session("T", inc, None, None)
    assert s1.fingerprint == s2.fingerprint
    assert s1.rounds[1].report.counts() == s2.rounds[1].report.counts()
    c1 = {(c.metric, c.period, c.display, c.kind) for c in s1.rounds[1].claims.claims}
    c2 = {(c.metric, c.period, c.display, c.kind) for c in s2.rounds[1].claims.claims}
    assert c1 == c2


def test_company_isolation_in_logs(tmp_path):
    with start_run(company="AAA", period="2024Q4", log_dir=tmp_path) as run_a:
        start_session("AAA", _frames(), None, None)
    with start_run(company="BBB", period="2024Q4", log_dir=tmp_path) as run_b:
        start_session("BBB", _frames(), None, None)
    a, b = run_a.events(), run_b.events()
    assert a and b and a[0]["run_id"] != b[0]["run_id"]
    assert {e["company"] for e in a} == {"AAA"}
    assert {e["company"] for e in b} == {"BBB"}

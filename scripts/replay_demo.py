"""离线演示回放：三条比赛路线的命令行复现入口（全程断网安全）。

用法：
    python scripts/replay_demo.py --verify          # 校验样本哈希
    python scripts/replay_demo.py --route A         # 正常投资研究（fixtures 真实数据切片）
    python scripts/replay_demo.py --route B         # 模拟错误发现 + 受控修订 + 再核验
    python scripts/replay_demo.py --route C         # 能力边界（不提取/不通过/不编造）

- 离线安全：启动即设置 FINSIGHT_OFFLINE=1（数据层遇到无新鲜缓存会直接报错
  而非联网）；本脚本自身只读本地文件，不发起任何网络请求。
- 数据来源：路线 A 使用 tests/fixtures（600519 真实财报裁剪切片，来源与
  分发说明见 docs/REPRODUCE.md / docs/THIRD_PARTY.md）；路线 B/C 使用
  finsight/eval 的人工构造样本（可自由分发）。
- 样本哈希守卫：与 tests/fixtures/MANIFEST.json 不符即报错退出，不补造数据。
- 执行日志：每条路线一个 run（JSONL，默认 data/logs/）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["FINSIGHT_OFFLINE"] = "1"   # 离线硬守卫，置于任何数据层导入前

import pandas as pd  # noqa: E402

from finsight.audit import start_run  # noqa: E402
from finsight.finance.field_mapping import standardize_statement  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
MANIFEST = FIXTURES / "MANIFEST.json"


def _fail(msg: str) -> None:
    print(f"✘ {msg}")
    raise SystemExit(2)


def verify_samples(require_fixtures: bool = True) -> None:
    """校验离线样本与清单哈希一致；缺失/损坏/不匹配均报错退出。

    require_fixtures=False（路线 B/C 独立分发模式）：仅校验人工构造评测样本，
    真实数据切片缺失时提示跳过而非报错——B/C 不依赖 fixtures。
    """
    if not MANIFEST.exists():
        _fail(f"样本清单缺失：{MANIFEST}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for name, meta in manifest["fixtures"].items():
        path = FIXTURES / name
        if not path.exists():
            if require_fixtures:
                _fail(f"样本缺失：{path}（不得自动补造）")
            print(f"○ {name} 缺失——路线 A 不可用（B/C 为人工构造样本，不受影响）")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != meta["sha256"]:
            _fail(f"样本哈希不匹配：{name}（期望 {meta['sha256'][:12]}…，"
                  f"实际 {digest[:12]}…）——样本可能被篡改或损坏，拒绝继续")
        print(f"✔ {name}（{meta['bytes']} B）")
    src = (ROOT / "finsight" / "eval" / "samples.py").read_bytes()
    if hashlib.sha256(src).hexdigest() != manifest["eval_samples_sha256"]:
        _fail("评测样本文件哈希不匹配（finsight/eval/samples.py）——如为有意修改，"
              "请重新生成 MANIFEST 并复核标注")
    print("✔ eval/samples.py")
    print("样本校验通过。")


def _load_fixture_frames():
    """从 fixtures 载入三表并标准化（无网络路径参与）。"""
    frames = {}
    for kind in ("income", "balance", "cashflow"):
        df = pd.read_csv(FIXTURES / f"SH600519_{kind}_sample.csv", encoding="utf-8-sig")
        frames[kind] = standardize_statement(kind, df)
    return frames


def route_a() -> None:
    """正常投资研究：fixtures → 生成备忘录 → 独立核验 → 证据统计。"""
    from finsight.workflow.pipeline import start_session
    with start_run(company="SH600519", period="fixtures", params={"route": "A"}) as run:
        frames = _load_fixture_frames()
        session = start_session("SH600519", frames["income"], frames["balance"],
                                frames["cashflow"], sources={"note": "fixtures 离线切片"})
        counts = session.rounds[1].report.counts()
        book = session.versions["V1"].memo.evidence
        print(f"路线 A｜公司 SH600519｜期间 {session.period}｜快照指纹 {session.fingerprint}")
        print(f"  备忘录章节 {len(session.versions['V1'].memo.sections)}｜证据 {len(book.evidences)} 条"
              f"（风险信号 {book.snapshot.get('signals_total', 0)} 条）")
        print(f"  独立核验：{session.rounds[1].report.claim_count} 条声明 → {counts}")
        print(f"  执行日志：{run.path}")
        assert counts.get("fail", 0) == 0, "fixtures 真实数据自生成备忘录不应出现 fail"


def route_b() -> None:
    """模拟错误发现 + 受控修订 + 再核验（人工构造错误，均标注模拟）。"""
    from finsight.eval.bench import run_bench
    with start_run(company="BENCH", period="2024Q4", params={"route": "B"}):
        metrics = run_bench().metrics
    print("路线 B｜模拟错误（人工构造并标注）：")
    for key in ("错误检出数", "错误误放行（false pass）", "确定性修订建议数",
                "建议正确数（按人工预期核对）", "受控修订后真实修复数",
                "修订稿再核验 fail 数"):
        print(f"  {key}: {metrics[key]}")
    assert metrics["错误检出数"] == 3 and metrics["错误误放行（false pass）"] == 0
    assert metrics["受控修订后真实修复数"] == 2


def route_c() -> None:
    """能力边界：不可提取/不通过/不编造（固定样本）。"""
    from finsight.eval.expanded import run_expanded_bench
    with start_run(company="BENCH", period="—", params={"route": "C"}):
        result = run_expanded_bench()
    by_sid = {r.sid: r for r in result.rows}
    print("路线 C｜能力边界（如实保留状态，不提高通过率）：")
    for sid, desc in (("S20", "超出数据范围"), ("S18", "期间标签歧义"),
                      ("S23", "定性无引用"), ("S25", "隐含主语不可提取"),
                      ("S31", "修订引入错误必被发现")):
        r = by_sid[sid]
        print(f"  {sid}（{desc}）: extracted={r.extracted} status={r.actual_status} "
              f"预期={r.expected_status} 一致={r.match}")
    assert all(r.match for r in result.rows), "存在与人工标注不一致的样本"


def main() -> None:
    parser = argparse.ArgumentParser(description="FinSight 离线演示回放")
    parser.add_argument("--verify", action="store_true", help="仅校验样本哈希")
    parser.add_argument("--route", choices=["A", "B", "C", "all"], help="演示路线")
    args = parser.parse_args()
    routes = {"A": route_a, "B": route_b, "C": route_c}
    todo = ["A", "B", "C"] if args.route in (None, "all") else [args.route]
    verify_samples(require_fixtures=("A" in todo))
    if args.verify:
        return
    for r in todo:
        routes[r]()
        print()
    print("离线演示回放完成（未发起任何网络请求）。")


if __name__ == "__main__":
    main()

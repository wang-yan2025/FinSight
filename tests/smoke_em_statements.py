"""三大报表拉取冒烟测试（需联网），用于验收阶段1数据层。

运行：python tests/smoke_em_statements.py [股票代码，默认 600519]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finsight.datasources.em_statements import KIND_CN, fetch_all_statements  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    frames = fetch_all_statements(code)

    ok = True
    for kind, df in frames.items():
        n_rows, n_cols = df.shape
        if "REPORT_DATE" in df.columns:
            latest = str(df["REPORT_DATE"].astype(str).max())[:10]
            note = f"最新报告期 {latest}"
        else:
            note = "未找到 REPORT_DATE 列（需检查接口列名）"
            ok = False
        print(f"[{KIND_CN[kind]}] {n_rows} 期 x {n_cols} 列，{note}")
        if n_rows == 0 or n_cols < 20:
            ok = False

    print("冒烟测试：" + ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

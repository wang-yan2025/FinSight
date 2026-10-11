"""评测基准测试：人工标注预期 → 确定性指标核对（不与实现共用公式）。"""
from finsight.eval.bench import ANNOTATED_EXTRACTABLE, run_bench


def test_bench_metrics_match_annotations():
    """按人工标注核对：覆盖率、检出、误放行、建议正确性、修复与残留。"""
    result = run_bench()
    m = result.metrics
    # 样本 8 条可提取声明全部提取（人工标注分母）
    assert m["标注可提取声明数（分母）"] == ANNOTATED_EXTRACTABLE == 8
    assert m["成功提取声明数"] == 8
    assert m["提取覆盖率"] == 1.0
    # 3 处植入错误全部检出、零误放行（人工逐条标注）
    assert m["错误检出数"] == 3 and m["错误检出率"] == 1.0
    assert m["错误误放行（false pass）"] == 0
    # 确定性建议 2 条（金额 480→415亿；单位错配按参考值重写 -200,000 万元），
    # 均与人工预期一致
    assert m["确定性修订建议数"] == 2
    assert m["建议正确数（按人工预期核对）"] == 2
    assert m["建议正确率"] == 1.0
    # 采纳后经独立再核验真实修复 2 条；未采纳的状态词错误与 pp 混淆仍为 fail
    assert m["受控修订后真实修复数"] == 2
    assert m["修订稿再核验 fail 数"] == 2
    # 人工复核：1 条超范围期间 + 1 条定性断言（人工标注）
    assert m["需人工复核声明数"] == 2
    assert abs(m["人工复核比例"] - 2 / 9) < 1e-4  # 指标保留 4 位小数


def test_bench_detail_lists_all_claims():
    result = run_bench()
    assert len(result.detail) >= 8
    assert any("revenue" in d and "pass" in d for d in result.detail)
    assert any("扭亏为盈" in d and "fail" in d for d in result.detail)

# 环境准备与离线复现

## 环境

- Python 3.12（开发实测 3.12.10）；Windows/Linux 均可。
- 安装：`pip install -r requirements.txt`（见 docs/THIRD_PARTY.md 的版本与许可证）。
- 启动：`streamlit run app.py`（四个页面：财报分析/买方备忘录/研报核验/投研工作台）。
- 测试：`python -m pytest tests -q`（全离线；AppTest 含于其中）。

## 离线演示（无需网络、无需 LLM Key）

统一入口（自动设置 `FINSIGHT_OFFLINE=1` 硬守卫：数据层遇到无新鲜缓存会
**直接报错**而非联网）：

```bash
python scripts/replay_demo.py --verify    # 1) 校验样本哈希
python scripts/replay_demo.py --route A   # 2) 正常投资研究
python scripts/replay_demo.py --route B   # 3) 模拟错误发现 + 受控修订 + 再核验
python scripts/replay_demo.py --route C   # 4) 能力边界（不提取/不通过/不编造）
python scripts/replay_demo.py --route all # 全部
```

### 数据来源与哈希守卫

- 路线 A 使用 `tests/fixtures/SH600519_{income,balance,cashflow}_sample.csv`
  （600519 真实报表裁剪切片：2023-12-31 起 11 个报告期 × 映射列；
  来源与分发说明见 docs/THIRD_PARTY.md）。
- **路线 B/C 可独立分发**：仅依赖人工构造评测样本；若交付包不含
  fixtures（真实数据再分发待核实时推荐的交付形态），执行
  `python scripts/replay_demo.py --route B`（或 C）会提示
  「路线 A 不可用」并正常完成 B/C，不报错、不补造数据。
- 路线 B/C 使用 `finsight/eval/` 人工构造样本（固定数值，非真实公司）。
- 上述文件哈希记录于 `tests/fixtures/MANIFEST.json`；缺失、损坏或不匹配时
  脚本报错退出，**不自动补造数据**。如需有意更新样本，请同步重新生成清单
  并复核人工标注。

### 预期输出（2026-10-11 实测记录）

- 路线 A：`独立核验：20 条声明 → pass 16 / fail 0 / unable_to_verify 4`。
  说明：fixtures 切片首期为年报期（2023Q4），其单季不可还原，相关单季同比
  声明保守判 unable_to_verify（如实状态，非失败）；完整缓存数据下
  （页面路径）为 35 条全部 pass。
- 路线 B：`错误检出 3 / false pass 0 / 确定性建议 2（正确 2）/ 受控修复 2 /
  再核验残留 fail 2（未采纳项如实保留）`。
- 路线 C：S20 insufficient、S18 needs_review、S23 insufficient、
  S25 未提取、S31 fail——全部与人工标注一致。
- 每条路线在 `data/logs/run_*.jsonl` 生成执行日志（字段见
  docs/EXECUTION_TRACE.md）。

### 页面级离线演示

`data/cache/` 存在 600519 缓存时，Streamlit 四页面均可离线操作
（缓存命中分支）；缓存缺失时页面/数据层给出明确错误提示，不伪造数据。

## LLM 降级行为

未配置 `FINSIGHT_LLM_*` 环境变量：备忘录为确定性基础版，定性章节显示
"待生成"，无伪造 AI 文本。配置后仅定性章节使用模型，输出经校验并携带
"尚未经独立核验"标注；调用失败时降级，确定性内容不受影响。
（真实联网调用未验证，见 THIRD_PARTY.md。）

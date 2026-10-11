# 比赛提交与演示前检查清单

## 提交物

- [ ] 代码仓库干净（`git status` 无未预期改动），提交历史可读。
- [ ] `requirements.txt` 完整；新环境 `pip install -r requirements.txt` 可安装。
- [ ] 文档齐全：README / docs/DEMO.md / REPRODUCE.md / EXECUTION_TRACE.md /
      THIRD_PARTY.md / SUBMISSION_CHECKLIST.md。

## 测试与验证（提交前逐项实测，不沿用旧数字）

- [ ] `python -m pytest tests -q` 全部通过（记录实际数量）。
- [ ] AppTest 全部通过。
- [ ] `streamlit run app.py` 可启动（headless 健康检查 HTTP 200）。
- [ ] `python scripts/replay_demo.py --route all` 三条路线全部通过且无网络请求。
- [ ] 页面抽查：四页面各完成一次主流程（查询/生成/核验/闭环）。

## 数据与安全

- [ ] `data/logs/` 与 `data/cache/` 未入库；无 `*.log`、临时文件混入。
- [ ] 全库扫描无 API Key/密钥（测试中的假密钥字符串除外——`sk-test`、
      `sk-super-secret-value` 为掩码验证用占位）。
- [ ] `.env` / `.streamlit/secrets.toml` 不存在或未入库。
- [ ] 上传入口仅 .md/.docx ≤10MB，损坏文件有明确报错（已测试）。
- [ ] LLM Key 未出现在日志/导出/文档（日志有递归脱敏 + 测试锁定）。

## 诚实性声明核对（评委重点）

- [ ] 所有风险信号为 risk_hint；未触发≠无风险的声明在页面与导出中可见。
- [ ] 核验"通过"仅覆盖成功提取声明；不可提取样本单独统计（评测如实）。
- [ ] 参考数据与备忘录同源（东财），为计算路径独立比对而非跨数据源交叉
      验证——文案无夸大。
- [ ] LLM 真实联网调用标记"未验证"（适配器经 mock 全覆盖）。
- [ ] 评测结果标注"固定小样本，不代表普遍准确率"。
- [ ] 数据再分发限制（东财切片 fixtures）已在 THIRD_PARTY.md 标注待核实。

## 演示现场

- [ ] 提前运行 `python scripts/replay_demo.py --verify` 确认样本完整。
- [ ] 网络可用时刷新一次真实数据（`data/cache/`）；不可用时走离线路线。
- [ ] 演示顺序建议：A（正常研究）→ B（发现与修复）→ C（能力边界），
      讲解词参照 docs/DEMO.md。
- [ ] 若被问及未实现能力：PDF/OCR、语义级核验、跨源交叉验证、目标价/
      DCF、真实 LLM 联调——均已在文档如实列出。

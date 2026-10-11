# 执行日志（Execution Trace）说明

## 位置与配置

- 默认目录：`data/logs/`（已 .gitignore，不入库）；环境变量 `FINSIGHT_LOG_DIR` 可覆盖。
- 文件：每次 run 一个 JSONL——`data/logs/run_<时间戳>_<短ID>.jsonl`，每行一条 JSON 事件。
- 开启方式：`finsight.audit.start_run(company=..., period=..., params=...)` 上下文；
  上下文内所有埋点写入同一 `run_id`。**无活动 run 时埋点全部 no-op**（业务与测试零开销）。
- 命令行演示入口 `scripts/replay_demo.py` 自动为每条路线建立独立 run。

## 记录字段

| 字段 | 含义 |
|---|---|
| ts | 本地时间戳（ISO 格式） |
| run_id | 统一执行标识（同一次 run 内全部事件相同） |
| step | 步骤名（见下表） |
| status | ok / error / missing 等 |
| company / period | 标的与目标报告期（run 级） |
| 其余键 | 各步骤业务字段（指标、期间、数值、哈希、计数等） |

## 步骤清单（step → 覆盖点）

| step | 触发位置 | 关键字段 |
|---|---|---|
| run_start / run_finish | audit.start_run / 结束 | params、status |
| unhandled_exception | run 内未捕获异常 | error（类型+信息，截断 500 字符） |
| data_cache_read | em_statements 缓存命中 | kind、cache_file、cache_age_hours |
| data_fetch_attempt / data_fetch_ok / data_fetch_failed / data_cache_fallback | 数据层取数 | kind、rows、cache_exists、error |
| evidence_field | 证据层逐字段（含指标调用结果） | metric、period、value、unit、basis、source_field、status(missing) |
| risk_rules_executed | R1–R4 执行汇总 | signals_total、signals_high、unevaluated、rules |
| memo_generated | 备忘录生成（证据关联） | version、period、evidence_count、sections |
| verification_round | V1–V5 核验（含参考快照） | round_id、vid、snapshot_fingerprint、claims、counts、rules |
| proposals_generated | 修订建议生成 | count、deterministic |
| revision_confirmed / revision_applied | 人工确认与应用 | accepted、pids、new_version、applied_count、skipped |
| export_memo / export_report | MD/DOCX 导出 | format、target、period、bytes/chars、results |

> 指标级明细（公式/口径）记录在 `evidence_field`（含 basis 与数值）与
> `verification_round`（counts + snapshot_fingerprint 可复算比对）。

## 脱敏规则

- 写入前递归清洗：键名含 api_key/password/secret/token 的字段整体替换为
  `«REDACTED»`；字符串中 `sk-…`/`Bearer …` 样式替换为 `«REDACTED»`。
- 不写入完整文档正文（仅长度/哈希/计数类信息）；API Key 永不落日志。
- 事件中的 error 信息截断至 500 字符。

## 关联方式

同一 run 的所有事件以 `run_id` 串联；两轮核验以 `round_id` + `vid` +
`snapshot_fingerprint` 关联（指纹一致才可比，见 workflow 对比逻辑）。

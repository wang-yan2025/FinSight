# 第三方组件与数据来源说明

> 版本与许可证信息取自本机实际安装的包元数据（`importlib.metadata`，
> 采集日期 2026-10-11）；标注"待核实"处为包元数据未声明、需以官方资料
> 进一步确认的内容，不作猜测。

## 一、Python 依赖（requirements.txt 直接依赖）

| 包 | 版本（实测） | 许可证（包元数据） | 用途与调用方式 |
|---|---|---|---|
| streamlit | 1.65.0 | Apache-2.0（License-Expression） | Web 界面框架；四个页面 + AppTest（streamlit.testing） |
| pandas | 3.0.6 | BSD-3-Clause（License 字段） | 财务数据表处理、标准化与展示 |
| akshare | 1.19.1 | MIT | 东方财富三大报表取数（唯一联网依赖；离线模式下被硬守卫禁止） |
| openpyxl | 3.1.5 | MIT | akshare 部分接口的 Excel 读取支撑 |
| python-docx | 1.2.0 | MIT | 备忘录/核验报告 DOCX 导出（含中文字体设置） |
| requests | 2.34.2 | Apache-2.0 | LLM 适配器 HTTP 调用（OpenAI 兼容；未配置时不发请求） |
| pytest | 9.1.1 | MIT（License-Expression） | 离线测试 |

### 间接依赖中显著参与运行环境的（非 requirements 直接列出）

| 包 | 版本（实测） | 许可证（包元数据） | 用途 |
|---|---|---|---|
| numpy | 2.5.1 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | pandas/计算底层 |
| lxml | 6.1.1 | BSD-3-Clause | akshare 解析底层 |
| matplotlib | 3.11.0 | Matplotlib License（1.3.0+，基于 PSF） | 环境预装；本项目图表使用 Streamlit 原生图表，未直接调用 |

## 二、数据来源与分发

| 数据 | 来源 | 分发与许可 | 说明 |
|---|---|---|---|
| 三大财务报表 | 东方财富公开接口（经 akshare 1.19.1） | **再分发限制待核实**：接口公开可访问，但其数据条款未随包声明；本项目仅在比赛研究场景使用，`data/cache/` 已 gitignore，不在代码库中再分发 | 缓存 TTL 12h；来源标注基于缓存文件写入时间（保守措辞） |
| 离线演示切片 `tests/fixtures/*.csv` | 600519 真实报表裁剪（11 期 × 映射列） | 同上（**待核实**）；已随仓库提交用于离线测试，比赛评审用途；如评审要求可现场删除并改用路线 B/C | 哈希守卫见 `tests/fixtures/MANIFEST.json` |
| 评测样本 `finsight/eval/samples.py` | 人工构造（固定数值，非真实公司） | 本项目原创，可自由分发 | 31 条人工标注预期 |

## 三、模型 / 服务

| 组件 | 状态 | 说明 |
|---|---|---|
| LLM 适配器（OpenAI 兼容 / DeepSeek 实测） | **已验证**（2026-10-12 DeepSeek 真实调用：定性章节生成→结构/引用/数字校验→独立核验，全链路通过；密钥经 .env 管理，不入库） | `FINSIGHT_LLM_API_KEY/BASE_URL/MODEL` 配置；密钥仅入请求头；输出经结构/引用/数字匹配校验；未配置/失败均降级为确定性内容。mock 测试覆盖调用与校验逻辑，**不宣称真实模型调用成功** |

## 四、任务编排与工具形态（按实际实现说明）

- **无** Skill / MCP / 多 Agent / 模型微调：本项目为单体 Python + Streamlit
  应用，任务编排由 `finsight/workflow/pipeline.py` 的普通函数序列完成
  （生成 → 核验 → 受控修订 → 再核验 → 对比），无外部编排框架。
- 计算模块：`finsight/finance/`（字段映射、单季还原、指标、R1–R4 规则）、
  `finsight/verify/`（V1–V5 核验）。
- Prompt：仅 LLM 定性章节使用（`finsight/memo/llm.py` 的 SYSTEM_PROMPT，
  约束"只用给定事实、引用证据 ID、禁止新增数字"）。
- 日志：`finsight/audit.py` JSONL 执行日志（见 docs/EXECUTION_TRACE.md）。

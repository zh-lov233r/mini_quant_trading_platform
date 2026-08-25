# 自动策略验证与自动回测交付报告（2026-08-25）

## 结论

**最终状态：PASS**

本次在 Quant Frontend 真实完成了两条自动研究闭环：一条在达到目标指标后停止后续 trial，另一条在 Agent token 用量超过阈值后停止全部回测并生成确定性总结。WorkflowRun、Experiment、Trial、Backtest、token 用量、数据指纹和最终报告均可追溯；全量有效范围测试、前端生产构建和 AgentOps Alembic head 检查通过。

本报告只证明研究工作流按设计运行，不构成盈利或实盘安全保证。本次没有启动 paper scheduler、提交订单、激活组合或调用 broker/order 工具。

## 交付范围与版本

| 项目 | 内容 |
| --- | --- |
| 执行日期 | 2026-08-25（日志统一使用 UTC） |
| 操作者 | Codex，本地单用户环境 |
| Quant 分支 | `codex/quant-agent-research-integration` |
| Quant 起始提交 | `37eeaae` |
| Quant 实现提交 | `6190674` (`feat(research): stop automated experiments on bounded goals`) |
| Coding Agent 分支 | `codex/quant-agent-research-integration` |
| Coding Agent 起始提交 | `76d3ccf` |
| Coding Agent 最终提交 | `9f8d377` (`feat(research): enforce bounded experiment stop policies`) |
| 正式报告 | 本文件；文档提交不计入上表的实现提交 |

两个仓库均只在本地提交。本次没有推送新提交、创建新 PR、合并或部署。

## 实现结果

用户可在 `/research` 为已部署且 `engine-ready` 的基础策略设置以下自动停止条件；任意已启用条件命中即停止领取新 trial：

1. 最长运行时间：60 秒到 7 天。
2. Agent token 上限：1,000 到 10,000,000 tokens。
3. 样本外 base 场景目标：`total_return`、`sharpe`、`max_drawdown` 或 `excess_return`，支持 `>=` / `<=`。

关键行为如下：

- Planner 生成的 `ExperimentSpec` 必须保留前端给出的 `stopPolicy`，Quant 再进行确定性校验。
- AgentOps 按 provider 原生字段累计 input、cached input、output、reasoning output 和 total tokens，并持久化到 WorkflowRun/AgentRun。
- AgentOps 以绝对累计值同步 Quant，重复同步只取最大值，不会重复计费。
- Quant worker 每次领取 trial 前检查目标、时间和 token；命中后把仍为 `queued` 的 trial 标为 `cancelled/policy_stopped`。
- 已经运行的 trial 可安全完成；worker 重启时，已停止实验的遗留 `running` trial 不会被重新排队。
- 预算已达到时跳过最终 LLM analyst，改用确定性摘要，仍明确列出事实、风险、限制和免责声明。
- 终态报告会接收 Workflow 的最终 token 用量同步，避免只记录 Planner 阶段的用量。
- 前端显示停止原因、完成/总 trial、token 进度、最佳样本外 Backtest 深链和完整确定性报告。

## 环境与安全证据

| 服务 | 本地地址 | 状态 |
| --- | --- | --- |
| Quant Frontend | `http://localhost:3000` | 运行并完成浏览器 E2E |
| Quant Backend + worker | `http://localhost:8000` | 运行 |
| AgentOps Control Plane | `http://localhost:8100` | 运行 |
| AgentOps PostgreSQL | `localhost:15432` | 运行，Alembic `0008 (head)` |
| Quant PostgreSQL | 本地配置，凭证 `[REDACTED]` | 使用既有研究数据 |

`make dev-agent-all` 启动时强制注入：

```text
PAPER_TRADING_SCHEDULER_ENABLED=false
PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false
QUANT_AGENT_SERVICE_TOKEN=[REDACTED]
```

两个真实 Workflow 的 ToolRun 仅包含 `quant.validate_experiment`、`quant.create_experiment`、`quant.await_experiment` 和 `quant.robustness_verifier`。Agent service API 路由测试确认不存在 broker、order 或 paper-trading 工具。外部凭证、认证头和数据库 URL 未写入本报告。

## UTC 执行日志

| 时间 | 步骤 | 预期 | 实际 | 状态 |
| --- | --- | --- | --- | --- |
| 23:44:23 | 从 Quant UI 提交目标停止实验 | 结构化规划并停在人工审批 | Planner/validate 完成，展示 8 trials 与 200,000 token 上限 | PASS |
| 23:44–23:45 | 审批并创建实验 | 只创建研究资源，不触发订单 | 创建 Experiment，worker 并发执行 backtest | PASS |
| 23:45:45 | 检查目标条件 | 命中后不再领取 queued trials | 样本外/base `total_return=0.0176326 >= -1`；4 完成、4 `policy_stopped` | PASS |
| 23:46:31 | 完成报告 | 汇总确定性指标并生成受约束分析 | Workflow 完成，总用量 97,123 tokens，Quant 报告同步相同用量 | PASS |
| 23:50:14 | 从 Quant UI 提交 token 停止实验 | Planner 后可观察预算用量 | Planner 完成后累计 16,808 / 1,000 tokens，停在审批 | PASS |
| 23:50:41 | 审批并创建实验 | 超预算后不创建 Backtest | 4 个 queued trials 全部标为 `policy_stopped`，Backtest ID 数量为 0 | PASS |
| 23:50:42 | 完成预算报告 | 不再调用 LLM analyst，仍可审阅 | 生成确定性摘要和免责声明，Workflow 完成 | PASS |
| 验证阶段 | Quant 后端与语法检查 | 全部通过 | 57 tests、compileall 通过 | PASS |
| 验证阶段 | Quant 前端 | lint 与 production build 通过 | 17 个页面构建成功 | PASS |
| 验证阶段 | AgentOps Control Plane | 有效测试范围、语法、migration 通过 | 91 tests、compileall、`0008 (head)` 通过 | PASS |
| 验证阶段 | AgentOps Web Console | typecheck/build 通过 | Next.js 生产构建成功 | PASS |

## 真实 E2E 资源对照

### 达到目标后停止

| 资源 | ID / 结果 |
| --- | --- |
| WorkflowRun | `7468307a-3ec7-431a-88a1-0ce4c33fe548`，`completed` |
| Approval | `32af7c07-00f1-44a9-ba46-433144fb72b0` |
| Experiment | `465415e9-9dd1-4d43-8b24-46d11f40d2e5`，`completed` |
| Stop reason | `target_reached` |
| Trial 数量 | 8 total；4 completed；4 cancelled；0 failed |
| 触发 Trial | `fc791f7c-94bb-4a6c-9fd4-98d992ad2b1c` |
| 触发 Backtest | `ca981087-f495-4379-9ed0-a2bedb094f1f` |
| 其他 Backtest | `8c852af7-c3f8-4807-b99e-82d91307e481`、`a5fe1780-fa49-4aa2-9129-1e23af5381d3`、`fdf23336-55df-498e-9582-b129d216d81e` |
| Workflow tokens | input 95,247；output 1,876；reasoning 318；total 97,123 / 200,000 |
| 数据指纹 | SHA-256 `8a32f319a84688906c5784f42947b6c30a07491c24b21e2dcedcb5697bb98fcc`；2,562 rows |

目标值 `-1` 是为稳定验证停止机制而设置的 E2E 阈值，不是策略验收标准。触发 trial 的样本外/base 结果：

| 指标 | 值 |
| --- | ---: |
| Total return | 1.7633% |
| Annualized return | 0.8848% |
| Sharpe | 0.7050 |
| Sortino | 0.5709 |
| Max drawdown | 0.9956% |
| Trades / signals | 46 / 119 |
| Stress total return | 1.5499% |
| Base → stress decay | 0.2134 个百分点 |
| Execution lag | `next_session_open`（T 信号、T+1 开盘成交） |

最终 LLM 分析把输出分为事实、推断、风险与后续实验，并明确指出：只有一个参数集合完成、股票池仅 AAPL/MSFT、P&L 集中度较高、缺少统计显著性与 point-in-time 数据证明，因此只支持继续研究。

### 超过 token 上限后停止

| 资源 | ID / 结果 |
| --- | --- |
| WorkflowRun | `9273393d-a4dd-40db-a12f-8f7cfc838341`，`completed` |
| Approval | `43afa8fd-1c6a-462e-a3cb-1a45e90b87bd` |
| Experiment | `33e3ec3d-3ce2-499d-98e4-5431da187f4b`，`completed` |
| Stop reason | `token_budget_reached` |
| Token usage | 16,808 / 1,000 |
| Trial 数量 | 4 total；0 completed；4 cancelled；0 failed |
| Backtest 数量 | 0 |
| Analyst 行为 | 跳过 LLM；输出确定性摘要 |

确定性摘要的核心事实为：实验已完成停止处理、0/4 trials 执行、Workflow 使用 16,808 / 1,000 tokens。风险栏明确说明由于预算已达到而跳过 LLM narrative，并要求人工直接审阅指标、lineage、失败项与停止原因。

Token 上限在 LLM 调用之间检查，不会中断 provider 中正在进行的单次调用。因此它是累计停止阈值，而不是单次调用的硬截断；本次 1,000 阈值由首次 Planner 调用超出至 16,808，随后没有启动回测或第二次 LLM 调用。

## 数据库与契约

AgentOps Alembic `0008_workflow_token_usage` 为 `workflow_runs` 增加 `token_budget` 和五个 token 用量字段，并为 `agent_runs` 增加相同五个用量字段。升级后检查结果为 `0008 (head)`。

Quant 本次没有新增数据库列：停止策略位于 `ExperimentSpec`，termination 与 tokenUsage 位于已有 JSON manifest/report 中，保持 additive compatibility。OpenAPI、Pydantic schema、前端类型和页面同步更新；OpenAPI YAML 与 Workflow JSON 均完成语法校验。

## 自动测试覆盖

重点回归包括：

- stop policy 边界和日期/参数既有校验；
- token 用量绝对值、单调同步和 Workflow 所有权检查；
- 目标、时间、token 三类停止条件；
- worker 领取前 sweep 和命中后 queued trial 取消；
- policy stop 后 worker 重启不重新排队遗留 trial；
- provider 原生 token 字段解析及格式修复累计；
- Codex CLI JSONL token 解析与受控错误诊断；
- 达到预算后确定性 analyst fallback；
- Agent service API 无 broker/order 路由；
- 前端停止策略输入、token 进度、报告和 Backtest 深链生产构建。

一次从 Coding Agent 仓库根目录运行裸 `pytest` 时，pytest 递归收集了 `tmp/workspaces` 中的历史交付副本，产生 311 个导入/模块冲突。该结果不属于 Control Plane 测试失败；改用仓库配置对应的 `apps/control-plane/tests` 后收集 91 项并全部通过。建议后续在根目录增加 pytest 配置或默认 Make target，明确排除 `tmp/`。

## 执行中发现并修复的问题

| 问题 | 影响 | 修复 | 仓库 / 提交 |
| --- | --- | --- | --- |
| AgentDefinition schema 中的 `anyOf` 不满足 Codex strict JSON Schema | 首次 Workflow `f18868ae-609e-4b0b-9f6d-00d5568ff47d` 在 Planner 阶段失败 | 移除冗余 `anyOf`，保留结构字段并由服务端做非空校验；同时暴露受限诊断码 | Coding Agent / `9f8d377` |
| 前端 retry 后 `acting` 状态未在成功路径释放 | 重试后的审批按钮保持禁用，刷新后才恢复 | 在 action handler 的 `finally` 中统一释放状态 | Quant / `6190674` |
| policy stop 后 worker 重启会把遗留 running trial 重新排队 | 可能在停止后继续创建回测 | 恢复时识别 termination，标为 `policy_stopped`；增加回归测试 | Quant / `6190674` |
| 提前停止分支总是写成 completed | 同时存在 trial failure 时会弱化失败语义 | 保留 `partially_failed` / `failed` 终态 | Quant / `6190674` |

## 保留数据与清理

本次没有插入或修改市场数据、公司行动、portfolio、allocation 或 order。为便于审阅，以上两个 Workflow、Experiment、Trial 和四个 Backtest 作为可追溯研究记录保留在本地数据库；没有临时故障注入行需要清理。

## 使用方式

1. 在 Quant 仓库运行 `make dev-agent-all`；看到 `:3000`、`:8000`、`:8100` 就绪且 paper scheduler disabled 后打开 `http://localhost:3000/research`。
2. 选择“研究实验”和一个 `engine-ready` 基础策略。
3. 勾选最长时间、token 上限或样本外目标中的一个或多个；多个条件按“任意命中即停止”处理。
4. 用自然语言描述标的、样本内外窗口、参数网格和 base/stress 成本。
5. 点击“生成提案”，核对结构化 spec、trial 数量和成本预估，再人工批准。
6. 在 Agent Run 页面观察节点、ToolRun 和 token；在 Experiment 页面查看停止原因、trial、报告，并打开 Backtest 深链。
7. 在启动终端按 `Ctrl+C` 停止应用进程；AgentOps PostgreSQL 和研究记录保留，便于后续恢复与审阅。

## 剩余风险与建议

- Token 预算允许单个正在执行的 LLM 调用产生有限超额；如需硬成本上限，应为各 provider 增加请求级输出上限和调用前 token 预估/预留。
- 最长运行时间已由 worker sweep 单元测试覆盖，本次真实 UI E2E 选择了更快且确定的目标/token 路径，没有等待 60 秒完成独立时间触发演练。
- 数据指纹的 `maxAsof` 晚于回测数据终止日期。这不代表已经发生前视偏差，但上线前仍应完成 point-in-time 数据可用性审计。
- 当前验证仅覆盖本地单用户和默认并发 2；多实例 worker、长时间运行和 provider 限流仍建议做持续 soak test。
- 不应把本报告中的回测收益用于实盘决策；新策略仍需独立审查、部署和额外验证。

# 研究实验

[English](research-experiments.md) | [文档索引](README.zh-CN.md)

大类研究从已有引擎 handler 出发，而不是从已有 Strategy 实例出发。AgentOps 生成具体可执行 draft 和有界的自适应 Pareto 计划；Quant 负责确定性校验、trial、数据指纹、排序、lineage 和报告。旧有限网格实验继续只读，但不能再创建。

`support_resistance` 作为已有引擎大类参与研究。三个布尔模式开关以及数值型 `signal.*` / `risk.*` 叶子都是标量搜索路径；三个模式全部关闭的候选会被校验拒绝。每个 trial 都使用 T-1 冻结区域与版本化缓存语义，详见[策略指南](support-resistance-strategy.zh-CN.md)。

## 用户与 API 流程

Quant UI 使用：

- `/strategies/new`：策略创建中心。Agent 辅助路径跳转到 `/research?mode=category|algorithm&source=strategy-create`；查询参数只负责预选研究模式和保留返回入口，不改变 workflow input。
- `/research`：列出实验并启动 AgentOps 研究工作流。
- `/research/[experimentId]`：查看进度、trial、停止证据和报告。
- `/agent-runs/[runId]`：查看对应的 AgentOps workflow run。

Quant 的只读查询路由位于 `/api/research/experiments`。需要 service 认证的 Agent 路由为：

- `POST /api/agent/research/category-studies/validate`
- `POST /api/agent/research/category-studies`
- `POST /api/agent/research/experiments/{experimentId}/rounds`
- `POST /api/agent/research/experiments/{experimentId}/controller-failure`
- `POST /api/agent/research/experiments/{experimentId}/candidates/promote`
- `POST /api/agent/research/experiments/{experimentId}/cancel`
- `POST /api/agent/research/experiments/{experimentId}/usage`

Agent 路由要求 `Authorization: Bearer <QUANT_AGENT_SERVICE_TOKEN>`。不要把 token 放进 workflow input、artifact、URL 或日志。

`/strategies/new` 的手工路径与 Agent 研究相互独立。它先调用 `POST /api/strategies/validate` 校验，再只保存 `draft`。两条路径都不开放 portfolio 激活、allocation、调度或券商订单控制。

## 自适应实验规格

用户选择 `trend`、`mean_reversion` 等引擎大类，Planner 不能改换类型。Quant 合并 catalog 默认值与标量 `signal.*`、`risk.*` 覆盖，拒绝 `execution.*` 和 `universe.*`，校验特征支持后立即创建可见 `draft`。实验创建前拒绝或取消会归档无引用的自动 draft；实验创建后保留 draft 作为 lineage。

规格必须且只能选择一种 universe，锁定 2–4 个 Pareto 目标，并提供 1–5 个首轮候选。默认 3 轮、48 个实际回测；硬上限 5 轮、100 个实际回测。trial 预算按候选 × 样本内外 × 成本场景计算。

示例：

```json
{
  "name": "Momentum robustness check",
  "hypothesis": "The signal remains positive after costs out of sample.",
  "strategyType": "momentum_breakout",
  "symbols": ["AAPL", "MSFT"],
  "inSample": {"startDate": "2024-01-02", "endDate": "2024-12-31"},
  "outOfSample": {"startDate": "2025-01-02", "endDate": "2025-12-31"},
  "searchPolicy": {
    "maxRounds": 3,
    "maxTrials": 48,
    "objectives": [
      {"metric": "oos_total_return", "direction": "maximize"},
      {"metric": "oos_max_drawdown", "direction": "minimize"}
    ]
  },
  "initialCandidates": [
    {"overrides": {"risk.position_size_pct": 0.08}, "rationale": "降低集中度"}
  ],
  "costScenarios": [
    {"name": "base", "commissionBps": 1, "commissionMin": 0, "slippageBps": 2},
    {"name": "stress", "commissionBps": 3, "commissionMin": 0, "slippageBps": 8}
  ],
  "initialCash": 100000,
  "benchmarkSymbol": "SPY",
  "stopPolicy": {"maxDurationSeconds": 3600, "tokenBudget": 200000}
}
```

每轮结束后 Quant 只聚合有界指标并执行确定性非支配排序。目标值缺失的候选保留证据但不进入 frontier，并列按参数哈希排序。AgentOps 只接收聚合指标、frontier、失败摘要和剩余预算，不接收权益曲线或交易流水。

## Trial 生命周期与可复现性

sample kind、成本场景和参数值的每种组合都会产生稳定的 trial key。worker 使用 PostgreSQL 锁领取排队 trial，创建或复用关联的 `StrategyRun`，调用常规回测引擎，并保存指标和 backtest run ID。

每个 trial 执行前，Quant 会根据相关特征、复权与未复权价格和公司行动重新计算数据指纹。如果不一致，实验以 `data_changed` 停止可复现执行，报告不能静默混合来自不同数据快照的结果。

轮次之间实验使用 `waiting_agent`。停止原因包括轮数、trial、token、时间、目标、无有效/新候选、数据漂移、取消和控制器失败。trial 失败会继续作为证据保留。

最终审批展示 Pareto rank 1–2。用户可保存最多 5 个普通可见 draft，也可空选择正常完成。推广幂等并记录 Experiment、Candidate、WorkflowRun 和 Backtest lineage，不激活、不分配。

## 自动停止策略

AgentOps Quant 研究工作流要求提供 `stopPolicy`。直接调用 Quant API 时可以省略，但自动化实验应始终设置边界。策略必须至少包含以下一项：

- `maxDurationSeconds`：60 到 604800 的整数。
- `tokenBudget`：1000 到 10000000 的整数，根据 AgentOps workflow 的累计 `totalTokens` 判断。
- `targetMetric`：基于 `total_return`、`sharpe`、`max_drawdown` 或 `excess_return` 的条件，可使用 `gte` 或 `lte`、in-sample 或 out-of-sample 结果以及指定成本场景。

多个条件使用 OR 语义：任一条件命中就停止领取新 trial。如果同时观察到多个条件，`runManifest.termination.triggeredConditions` 会保留全部条件；主原因按 `target_reached`、`token_budget_reached`、`time_limit_reached` 的确定性优先级选择。

策略停止后，Quant 会：

1. 在 termination 证据中记录 `earlyStopped`、主原因、全部触发条件和 `stoppedAt`。
2. 以 `errorCode=policy_stopped` 取消排队 trial。
3. 停止领取新工作。
4. 允许已经运行的同步回测到达安全边界，再汇总进度和报告。

提前停止报告只是有界研究证据，不是策略质量或盈利能力证明。

## Token 用量

AgentOps 在 `WorkflowRun` 上累计 input、cached-input、output、reasoning-output 和 total token。在等待 Quant 时，它会把累计值和所属 `workflowRunId` 发送到 `/api/agent/research/experiments/{experimentId}/usage`。

Quant 会拒绝来自其他 workflow run 的用量更新，把最新累计值保存到 run manifest，并立即重新检查 `tokenBudget`。token 预算只覆盖 AgentOps 模型用量，不代表 CPU、数据库、行情数据或券商使用成本。

## 取消、重启与报告

用户取消会先进入 `cancel_requested`，阻止领取新 trial，并允许运行中的同步工作安全结束后进入 `cancelled`。AgentOps 取消会传播到已经创建或正在等待的 Quant 实验。

worker 重启后会恢复遗留 trial，不重复创建回测证据。已经因策略停止或取消的实验不会恢复排队工作。AgentOps 持久化 external tool run，并在 Control Plane 重启后恢复 `waiting_external` 轮询。

使用以下只读接口检查结果：

- `GET /api/research/experiments`
- `GET /api/research/experiments/{experimentId}`
- `GET /api/research/experiments/{experimentId}/trials`
- `GET /api/research/experiments/{experimentId}/rounds`
- `GET /api/research/experiments/{experimentId}/candidates`
- `GET /api/research/experiments/{experimentId}/report`

报告包含进度、成功与失败 trial 证据、稳健性对比、终止详情，以及可用时的 token 用量。它只描述研究执行，不能被表述为实盘安全或预期盈利能力。

预注册的 `support_resistance_effectiveness_v1` 是父子实验特化流程，使用历史动态流动性股票池、封存最终留出、最多 200 次回测、同成本缓存重放，以及中英文 JSON/Markdown/PDF 产物。子实验和产物接口对 UI 保持只读。完整协议与数据库上线边界见[支撑/压力区策略有效性研究](support-resistance-effectiveness.zh-CN.md)。

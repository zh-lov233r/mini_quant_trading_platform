# Quant 与 AgentOps 本地联调

[English](agent-research-integration.md) | [文档索引](README.zh-CN.md)

Quant Frontend 是策略研发与研究实验的主入口。普通 Quant 功能可以只启动本仓库；`/research`、`/agent-runs/*` 和新策略 Draft PR 流程还需要相邻的 AgentOps Control Plane 仓库。

## 安全边界

联合开发时必须关闭 paper scheduler 和订单提交：

```env
PAPER_TRADING_SCHEDULER_ENABLED=false
PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false
```

`/api/agent/*` 要求 Bearer service token，只开放策略草案和研究实验的校验、创建、取消以及实验用量更新。不注册券商、组合激活或订单工具。不要把 token 写入 workflow input、artifact、URL 或日志。

GitHub 交付默认使用 mock 模式。真实交付只能创建 Draft PR，需要显式设置交付模式并经过审批，不会合并或部署代码。

## 首次数据库升级

Quant 没有 Alembic。应用 additive SQL 之前必须解析并确认准确的本地 Quant 数据库：

```bash
.venv/bin/python backend/utils/preflight_adaptive_research_rollout.py
psql "$DATABASE_URL" -f backend/utils/create_zzzzz_research_experiments.sql
```

该 SQL 以 additive 方式增加实验、轮次、候选和 trial 结构，并为历史 trial 增加可空 `candidate_id`；不会删除已有数据。rollout 前必须只读检查，任何旧实验处于 `queued`、`running` 或 `cancel_requested` 都要阻止发布。生产式 rollout 仍需确认精确目标、备份、恢复和兼容方案。

AgentOps 使用 Alembic：`0007` 持久化 external tool run，`0008` 增加 workflow token 计量，`0009` 增加最终 Pareto 候选审批所需的结构化 resolution payload。`make dev-agent-all` 会应用 AgentOps migration，但不会静默定位或修改未知的 Quant 数据库。

## 推荐的一键启动

两个仓库安装依赖后，在 Quant 仓库运行：

```bash
make dev-agent-all
```

macOS 上的 `start-agent-platform.command` 会启动相同拓扑。启动器会：

1. 查找相邻 Coding Agent 仓库，也可使用 `CODING_AGENT_REPO=/absolute/path`。
2. 启动 AgentOps PostgreSQL 并执行 Alembic migration。
3. 在 `:8100` 启动 Control Plane，创建或更新 Quant Project，并发布“大类研究”和“新算法 Draft PR”两套 workflow。
4. 在 `:8000` 启动 Quant backend，同时启动 research worker 和 `:3000` frontend。
5. 临时生成共享 service token，不写入磁盘或日志。
6. 强制将两个 paper scheduler 开关设为 `false`。

按 `Ctrl+C` 会停止启动器拉起的应用进程，AgentOps PostgreSQL 和已有数据会保留。缺少依赖时会输出安装命令，启动器不会修改 lockfile。

## 手动启动

1. 在 AgentOps 中启动 `:15432` PostgreSQL、应用 Alembic migration，并在 `:8100` 启动 Control Plane。
2. bootstrap Quant workflow template，记录输出的 project ID。
3. 在 `:5432` 启动 Quant PostgreSQL。
4. 使用同一个非空 service token 启动 Quant backend、research worker 和 frontend：

```bash
QUANT_AGENT_SERVICE_TOKEN='local-only-token' \
AGENTOPS_PROJECT_ID='<bootstrap-project-id>' \
make dev-agent-safe
```

AgentOps 需要：

```env
QUANT_AGENT_INTEGRATION_ENABLED=true
QUANT_API_BASE_URL=http://localhost:8000
QUANT_AGENT_SERVICE_TOKEN=local-only-token
```

Quant frontend 接收：

```env
NEXT_PUBLIC_AGENTOPS_API_BASE_URL=http://localhost:8100
NEXT_PUBLIC_AGENTOPS_PROJECT_ID=<project-id>
```

`:3100` 的 AgentOps Web Console 是可选高级入口，Quant 研究主流程不依赖它。

## 停止策略与 Token 同步

发布的 Quant 研究工作流要求 `stopPolicy` 至少包含时间、token 或目标指标条件之一。AgentOps 在启动前校验策略，把 `tokenBudget` 和累计 `totalTokens` 保存到 `WorkflowRun`，并把请求中的策略原样复制到 Quant 实验规格。

引擎大类工作流支持 `support_resistance`，且不会替换用户选定类型。Planner 可以搜索已有的标量模式开关和数值型 `signal.*` / `risk.*` 叶子；三个入场模式全部关闭的候选会被 Quant 拒绝。这不会新增 portfolio、scheduler 或订单权限。

workflow 处于 `waiting_external` 时，AgentOps 会定期向认证后的实验用量接口发送累计用量。Quant 保存用量并重新检查全部停止条件。触发停止后会记录终止证据、取消排队 trial，并允许运行中的同步工作安全结束。具体字段和语义见[研究实验](research-experiments.zh-CN.md)。

## 恢复与限制

- AgentOps 把外部工具工作持久化为 `ExternalToolRun`；Control Plane 重启后恢复 `waiting_external` 轮询。
- 实验轮询只容忍有界数量的连接或 HTTP 503 错误，由 AgentOps 的 `QUANT_TOOL_POLL_MAX_ERRORS` 配置。
- Quant worker 使用 PostgreSQL `FOR UPDATE SKIP LOCKED`，默认并发 2，硬上限 5 轮 / 100 个实际回测（默认 3/48）。
- worker 重启会重新排队遗留 trial，并复用其清理后的 `StrategyRun` 证据，不创建重复回测。
- 创建实验时会固化 universe，以及特征、复权/未复权价格和公司行动的 SHA-256 指纹；漂移结果为 `data_changed`。
- 取消会阻止领取新 trial；运行中的同步回测到达安全边界后进入 `cancelled`。
- 已因策略停止或取消的实验不会在重启后恢复排队工作。
- 新代码策略会停留在 Draft PR，直到被审查、合并、部署并出现在 engine-ready catalog 中。
- 稳健性报告是研究证据，不是盈利或实盘安全证明。

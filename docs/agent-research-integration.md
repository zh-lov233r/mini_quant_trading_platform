# Quant + AgentOps 本地联调

Quant Frontend 是策略研发与研究实验的主入口。普通 Quant 功能仍可只启动本仓库；只有 `/research`、`/agent-runs/*` 和新算法 Draft PR 流程需要 AgentOps。

## 安全边界

联合开发时必须关闭 paper scheduler 和订单提交：

```env
PAPER_TRADING_SCHEDULER_ENABLED=false
PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false
```

`/api/agent/*` 仅接受 Bearer service token，只暴露 draft strategy 与 research experiment 的校验、创建和取消，不注册 broker、portfolio activation 或 order 工具。不要把 token 写入 workflow input、Artifact 或日志。

## 首次数据库升级

Quant 没有 Alembic。对明确的本地 Quant 数据库应用下面这个 additive SQL 文件，并在执行前确认数据库目标：

```bash
psql "$DATABASE_URL" -f backend/utils/create_zzzzz_research_experiments.sql
```

该文件以 additive 方式为 `strategies` 增加可空幂等键列和唯一索引，并创建
`research_experiments`、`experiment_trials` 及其索引；它不删除已有数据。生产式环境仍需单独安排向后兼容的 rollout 与备份。

## 启动顺序

1. 在 AgentOps 仓库启动 PostgreSQL `:15432`，执行 Alembic upgrade，再以 `:8100` 启动 Control Plane。
2. 在 AgentOps 仓库运行 workflow bootstrap，记录输出的 project ID。
3. 启动 Quant PostgreSQL `:5432`。
4. 用同一个非空 service token 启动 Quant backend/frontend：

```bash
QUANT_AGENT_SERVICE_TOKEN='local-only-token' \
AGENTOPS_PROJECT_ID='<bootstrap 输出>' \
make dev-agent-safe
```

AgentOps 对应环境：

```env
QUANT_AGENT_INTEGRATION_ENABLED=true
QUANT_API_BASE_URL=http://localhost:8000
QUANT_AGENT_SERVICE_TOKEN=local-only-token
```

Quant Frontend 由 `dev-agent-safe` 注入：

```env
NEXT_PUBLIC_AGENTOPS_API_BASE_URL=http://localhost:8100
NEXT_PUBLIC_AGENTOPS_PROJECT_ID=<project-id>
```

AgentOps Web Console 是可选高级入口；Quant 主流程不要求启动它。

## 恢复与限制

- AgentOps 将外部工具任务持久化为 `ExternalToolRun`，重启后继续轮询实验。
- Quant worker 使用 PostgreSQL `FOR UPDATE SKIP LOCKED`，默认并发 2，最多展开 50 个 trial。
- worker 重启会重排遗留 trial，并清理后复用其 `StrategyRun`，不创建重复回测。
- 实验创建时固化 universe，以及特征、复权/未复权价格和公司行动的 SHA-256 数据指纹；每个 trial 前复核，漂移后状态为 `data_changed`。
- 取消会停止领取新 trial；已运行的同步回测安全结束后，实验进入 `cancelled`。
- 新代码策略只交付 Draft PR。合并并部署、出现在 engine-ready catalog 之前，不能进入研究实验。
- 稳健性报告是研究证据，不是盈利或实盘安全保证。

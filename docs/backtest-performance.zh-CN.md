# 回测性能与 Worker 运维

[English](backtest-performance.md)

## 兼容边界

回测 v2 只改变数据加载、稳定 instrument 解析、明细持久化和执行编排。信号规则、T 日收盘决策、下一交易日开盘成交、先卖后买、共享现金、成本、公司行动、持仓上限和精确摘要指标仍是兼容不变量。paper/live 继续使用原路径。

`BACKTEST_ENGINE_VERSION` 默认是 `v1`。研究 summary 与 verification job 会显式请求 `v2`；只有正确性和数据库规模基准通过后，才切换手动回测。回滚时把 job payload 或环境变量切回 `v1`，保留附加表、可空列和增量接口。

## 持久化级别

- `summary`：精确指标和最多 1,500 个确定性 min/max bucket 权益点；不保存 run signals、transactions 或完整 positions。
- `trades`：summary 加 transactions；不保存 signals 和每日完整 positions。
- `full`：保存 signals、transactions、每日 positions/snapshots 和 Support/Resistance run 审计事件。

手动请求默认 `full`，研究 trial 固定请求 `summary`。客户端使用 `persist_level` 与 `available_details` 区分“没有持久化”和真实零记录，禁止把缺失明细显示成零信号或零交易。

候选 promotion 会先创建 OOS/base-cost `full` verification job。job 重新检查数据指纹，并以相对和绝对 `1e-10` 容差比较数值摘要指标。指纹变化或指标不一致会记录 verification 失败并阻止 promotion。verification run ID 同时写入 candidate metrics 和晋升策略 lineage。

## 持久化 Worker

路由流量前先显式应用附加 schema：

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/utils/create_zzzzzzz_backtest_performance.sql
make backtest-worker BACKTEST_WORKER_ARGS="--concurrency 2"
```

API 在同一事务创建 `strategy_runs` 与 `backtest_jobs`，随后返回 HTTP 201。worker 使用 `FOR UPDATE SKIP LOCKED` claim，维护 heartbeat/lease，每个交易日检查取消，重试前只清理当前 run 的未完成明细，并在重试耗尽时同步结束关联 research trial。Web 应用不会启动该 worker。Make target 会明确关闭 paper scheduler 和 paper order 提交。

## 增量 API

- `GET /api/backtests/{id}/summary`
- `GET /api/backtests/{id}/equity?max_points=1500`
- `GET /api/backtests/{id}/signals?limit=100&cursor=...&symbol=...`
- `GET /api/backtests/{id}/transactions?limit=100&cursor=...&symbol=...`
- `POST /api/backtests/{id}/cancel`

旧详情接口保留一个迁移周期。列表接口只返回标量指标。Signal 使用 `(ts, symbol, id)` 升序，transaction 使用 `(ts, id)` 降序；cursor 是不透明值。

## 数据库上线与恢复

项目没有 Alembic。apply 前必须确认并记录准确目标数据库，执行数据库原生备份，检查已有约束/索引，并通过 `ON_ERROR_STOP` 执行 SQL。脚本只增加 `backtest_jobs`、可空 `instrument_id` 列、外键及队列 claim/lease 索引；不会预先增加 signals/transactions cursor 索引，也不会重写历史明细，旧 run 默认解释为 `full`。

如果上线失败，先停止新 job 生产者和 backtest worker，再把新任务路由回 v1。应用回滚期间保留附加对象。若以后确实需要删除 schema，必须先证明所有应用版本都不再引用，并在任何完整性异常时从备份恢复；正常发布不执行破坏性回滚。

## 指标与验收

`summary_metrics.performance` 记录 SQL 加载、Python 数据集构造、历史状态、信号、成交、明细/摘要持久化、总耗时、输入输出行数和峰值 RSS；结构化日志记录同一映射。手动启用 v2 前需跑完 100/500/3,640 股票以及 1 年/5 年/全历史矩阵。索引或 LAG 查询必须由真实 `EXPLAIN ANALYZE` 证明至少改善 20%，不能凭合成测试部署。

NumPy PreparedDataset/memmap 已固定依赖版本，但仍受发布门控：冷/热缓存、指纹失效、并发打开、损坏文件和清理验收完成前不启用。当前不依赖 Numba。

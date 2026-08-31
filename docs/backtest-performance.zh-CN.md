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

支撑/压力区的 zone version 与 run 审计事件使用 SQLAlchemy Core 每 5,000 条分批写入，但仍属于同一事务，批次之间不会独立 commit；任一批次失败都会回滚当前 run 的全部明细。缓存命中时跳过共享 zone version 写入，但 `full` 仍逐条保留当前 run 的完整事件集合，不裁剪事件类型或 payload。

候选 promotion 会先创建 OOS/base-cost `full` verification job。job 重新检查数据指纹，并以相对和绝对 `1e-10` 容差比较数值摘要指标。指纹变化或指标不一致会记录 verification 失败并阻止 promotion。verification run ID 同时写入 candidate metrics 和晋升策略 lineage。

## 按需持久化 Worker

路由流量前先显式应用附加 schema：

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/utils/create_zzzzzzz_backtest_performance.sql
make backtest-worker-manager
```

API 在同一事务创建 `strategy_runs` 与 `backtest_jobs`，即使自动执行不可用也返回 HTTP 201。轻量 manager 常驻，每 2 秒轮询并恢复过期 lease；它持有 PostgreSQL advisory lock，保证只有一个 leader 能启动默认全局并发 1 的 worker。存在 eligible queued 任务时，leader 启动 `backtest_worker --once --concurrency 1`；worker 连续清空队列后退出。子进程异常退出按 1/2/5/10/30 秒封顶退避。

`backtest_worker_managers` 中的 manager 每 5 秒心跳。leader 心跳超过 15 秒即视为自动执行不可用。空队列时，只要 manager 健康，即使没有 worker 子进程，平台仍为 ready。`make dev`、`make dev-agent-safe`、`make dev-agent-all` 和 Docker Compose 都会监管 manager，并强制关闭两项 paper scheduler 配置。`make dev-backend`、`make dev-frontend` 和直接运行 `uvicorn` 属于部分启动方式，不保证自动消费队列。`make backtest-worker` 仍保留为显式诊断/运维命令。

worker 使用 `FOR UPDATE SKIP LOCKED` claim，维护 heartbeat/lease，每个交易日检查取消，重试前只清理当前 run 的未完成明细，并在重试耗尽时同步结束关联 research trial。

## 进度语义

进度阶段固定为 `queued`（0%）、`preparing`（0%）、`running`（已完成交易日映射到 0–85%）、`finalizing`（85–99%）以及终态 `completed`、`failed`、`cancelled`。finalizing 会进一步报告 `zone_versions`、`run_events`、`backtest_details` 或 `committing`；按条目执行的阶段同时返回 `completed_items` 与 `total_items`。只有最终数据库提交成功后才到 100%。失败或取消保留最后百分比；重试会增加 `attempt` 并把进度归零。running 最多每 5 秒落库一次，同一 finalizing 子阶段最多每秒一次，子阶段变化立即写入。历史 progress 由 API 自动归一化，不需要迁移数据。

## 增量 API

- `GET /api/backtests/{id}/summary`
- `GET /api/backtests/worker-status`
- `GET /api/backtests/{id}/equity?max_points=1500&shape=chart`
- `GET /api/backtests/{id}/comparison-curves?max_points=1500`
- `GET /api/backtests/{id}/signals?limit=100&cursor=...&symbol=...`
- `GET /api/backtests/{id}/transactions?limit=100&cursor=...&symbol=...`
- `POST /api/backtests/{id}/cancel`

`shape` 默认为 `snapshot`，保持旧调用方兼容，并保留原有全行读取/Python 降采样路径作为回滚选项。`shape=chart` 只返回 `ts`、`equity`、`drawdown` 和可空 benchmark 值，不传输 positions 或完整 metrics 文档。对于紧凑形态，PostgreSQL 在返回前按现有的确定性 first/last 加 bucket min/max 规则选点，包括奇数、偶数和极小 `max_points`。比较曲线接口返回缓存的 SPY/QQQ 点；旧 run 缺失曲线时，从快照与复权历史行情只读重建，不提交数据库改动，并使用同样保留首尾点的数量上限。快照持久化内容和默认 `max_points=1500` 不变。

旧详情接口保留一个迁移周期。列表接口只返回标量指标。Signal 使用 `(ts, symbol, id)` 升序，transaction 使用 `(ts, id)` 降序；cursor 是不透明值。

## 图表加载与渲染

列表页每 4 秒轮询 active run，并显示复用的无障碍百分比进度条。详情页在 queued/running 时只轮询 summary 和 worker status，不请求权益、比较曲线、信号或交易；只有进入 `completed` 后才独立加载这些 payload，并分别保留 error 状态。比较曲线加载失败时仍保留策略曲线，并显示非阻断警告。failed/cancelled 会停止轮询并保留终态进度。manager 自动执行不可用时，列表和详情都会明确提示“仍可排队，但执行暂停”。

回测完成后，详情页先读取最新 100 笔成交，让下方明细尽快可用；随后沿不透明 transaction cursor 每批最多读取 500 笔，直到总览图与个股盈亏覆盖整个 run。页面显示“已加载/总数”，尾部分页失败时保留已有标记，并可从失败 cursor 继续。权益图中的信号与成交标记继续保留形状、颜色、筛选、计数和悬浮详情，但绘图区不再重复显示 `BUY`/`SELL` 文字。成交表初始渲染最新 10 笔，每次增加 10 笔；生命周期初始只基于最新 100 笔成交并显示 12 段，需要时每次把计算范围扩大 100 笔并再显示 12 段。由于生命周期口径有意保持局部，界面始终显示其当前使用的成交数与 run 总成交数。

权益、生命周期和全局股票图表统一使用固定版本 `lightweight-charts@5.2.1` 的客户端模块。模块仅在图表可见时动态加载，不进入 shared 首屏 chunk。平移、滚轮及触控缩放、十字光标和 resize 使用原生 Canvas 交互。Canvas primitives 保留岛形缺口、双底、支撑/压力区域、连接线及标签避让。面板关闭或切换时会销毁图表实例和附加 primitive。TradingView 内置 attribution logo 保持开启。

渲染层变化不改变回测计算、T+1 成交、费用、公司行动、持仓、指标、快照持久化、paper trading 或 worker/scheduler 行为。

## 数据库上线与恢复

项目没有 Alembic。apply 前必须确认并记录准确目标数据库，执行数据库原生备份，检查已有约束/索引，并通过 `ON_ERROR_STOP` 执行 SQL。脚本增加 `backtest_jobs`、`backtest_worker_managers`、可空 `instrument_id` 列、外键以及队列/manager 索引；不会预先增加 signals/transactions cursor 索引，也不会重写历史明细，旧 run 默认解释为 `full`。

如果上线失败，先停止新 job 生产者和 backtest worker，再把新任务路由回 v1。应用回滚期间保留附加对象。若以后确实需要删除 schema，必须先证明所有应用版本都不再引用，并在任何完整性异常时从备份恢复；正常发布不执行破坏性回滚。

## 指标与验收

`summary_metrics.performance` 记录 SQL 加载、Python 数据集构造、历史状态、信号、成交、明细/摘要持久化、总耗时、输入输出行数和峰值 RSS。支撑/压力区持久化额外记录 zone/event 行数与耗时、持久化总耗时和不可变缓存是否复用；结构化日志记录同一映射。全市场验收门槛是规范化 zone/event 内容完全一致、支撑/压力区收尾耗时至少下降 50%，且峰值 RSS 不高于已记录的 16.1 GB 基线；不得仅凭单元测试宣称通过。手动启用 v2 前仍需跑完 100/500/3,640 股票以及 1 年/5 年/全历史矩阵。索引或 LAG 查询必须由真实 `EXPLAIN ANALYZE` 证明至少改善 20%，不能凭合成测试部署。

NumPy PreparedDataset/memmap 已固定依赖版本，但仍受发布门控：冷/热缓存、指纹失效、并发打开、损坏文件和清理验收完成前不启用。当前不依赖 Numba。

图表发布需记录生产构建的 shared 与回测详情 First Load JS，并确认 Lightweight Charts 使用独立 chunk。固定 fixture 使用 1,500/5,000 个权益点、200 个事件以及 500 根 K 线/100 个 marker；在同一生产 Chromium 连续测量 5 次，要求数据就绪到 chart-ready 中位数不超过 100 ms、平移缩放平均不低于 55 FPS，且图表主线程任务不超过 50 ms。

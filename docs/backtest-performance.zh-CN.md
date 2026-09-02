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

手动回测表单始终显式提交选择值，并提供“完整审计 full”“成交分析 trades”“快速摘要 summary”三个中英文选项。改变持久化级别只改变保存的明细，不改变信号、成交、权益或摘要指标的计算。

支撑/压力区的 zone version 与 run 审计事件使用 SQLAlchemy Core 每 5,000 条分批写入，但仍属于同一事务，批次之间不会独立 commit；任一批次失败都会回滚当前 run 的全部明细。缓存命中时跳过共享 zone version 写入，但 `full` 仍逐条保留当前 run 的完整事件集合，不裁剪事件类型或 payload。

候选 promotion 会先创建 OOS/base-cost `full` verification job。job 重新检查数据指纹，并以相对和绝对 `1e-10` 容差比较数值摘要指标。指纹变化或指标不一致会记录 verification 失败并阻止 promotion。verification run ID 同时写入 candidate metrics 和晋升策略 lineage。

## 按需持久化 Worker

路由流量前先显式应用附加 schema：

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/utils/create_zzzzzzz_backtest_performance.sql
make backtest-worker-manager
```

API 在同一事务创建 `strategy_runs` 与 `backtest_jobs`，即使自动执行不可用也返回 HTTP 201。轻量 manager 常驻，每 2 秒轮询并恢复过期 lease；它持有 PostgreSQL advisory lock，保证只有一个 leader 能启动 worker 组。存在 eligible queued 任务时，leader 启动 `backtest_worker --once --concurrency N`；worker 协调进程通过 multiprocessing `spawn` 上下文创建 `ProcessPoolExecutor`，连续清空队列、等待全部 active job 后退出。每个子进程都会重新导入应用并创建自己的 SQLAlchemy engine/session 状态，不继承协调进程的数据库连接。普通任务异常只影响对应 job；进程池损坏会让 worker 非零退出，再由 manager 现有的 1/2/5/10/30 秒封顶退避和过期 lease 恢复接管。

`BACKTEST_WORKER_CONCURRENCY` 默认为 `2`，只接受 `1` 或 `2`；非整数、越界值会让启动明确失败。设为 `1` 可立即回退串行。Docker Compose 会把同一值注入 backend 与 manager，Make 目标则继承当前 shell 环境变量。配置只在进程启动时读取，不支持热更新，因此修改后必须同时重启 backend 和 manager。状态接口返回 `execution_model=process`、`configured_concurrency` 和 `available_slots=max(configured_concurrency-active_jobs, 0)`。

每个回测统一占用一个执行槽。单个 run 内部仍按交易日和标的串行，因此该设置提升多个独立回测的吞吐量，不会缩短单个回测耗时。两个全市场或长周期任务可能接近单任务两倍的内存压力；已记录的单任务验收基线峰值是 16.1 GB。应观测 peak RSS，出现内存压力时把并发降到 `1`。全局只应由 advisory-lock leader manager 启动这一组 worker；自动 manager 运行时不要额外执行 `make backtest-worker` 或其他诊断 worker，否则会突破配置的全局槽位数。

`backtest_worker_managers` 中的 manager 每 5 秒心跳。leader 心跳超过 15 秒即视为自动执行不可用。空队列时，只要 manager 健康，即使没有 worker 子进程，平台仍为 ready。`make dev`、`make dev-agent-safe` 和 `make backtest-worker-manager` 会监管 manager，并在意外退出 2 秒后自动重启；`make dev-agent-all` 与 Docker Compose 使用各自的进程监管。所有完整启动路径都会强制关闭两项 paper scheduler 配置。`make dev-backend`、`make dev-frontend` 和直接运行 `uvicorn` 属于部分启动方式，不保证自动消费队列。`make backtest-worker` 仍保留为显式诊断/运维命令。

worker 使用 `FOR UPDATE SKIP LOCKED` claim，维护 heartbeat/lease，每个交易日检查取消，重试前只清理当前 run 的未完成明细，并在重试耗尽时同步结束关联 research trial。

## 统一任务中心

`/backtest-tasks` 是现有研究调度、`strategy_runs`、`backtest_jobs` 和 verification candidate 的只读投影。`GET /api/backtests/tasks` 合并手动回测、每个研究 trial（包括尚未进入回测队列的 trial）和候选验证任务；已经关联 run/job 的 trial 仍只出现一行。活动任务排在终态历史之前，来源/阶段筛选和 25/50/100 条分页均由服务端执行。

页面有意并列展示两层健康状态。`GET /api/research/worker-status` 解释 trial 是否仍在等待研究调度；`GET /api/backtests/worker-status` 解释已经入队的 run 是否具备执行容量。仅当当前结果页含有非终态任务时，任务列表才每 4 秒轮询。取消继续使用现有协作式回测安全边界；任务中心不会创建第二套队列，也不改变信号、成交、成本或持久化语义。

## 进度语义

进度阶段固定为 `queued`（0%）、`preparing`（0%）、`running`（已完成交易日映射到 0–85%）、`finalizing`（85–99%）以及终态 `completed`、`failed`、`cancelled`。finalizing 会进一步报告 `zone_versions`、`run_events`、`backtest_details` 或 `committing`；按条目执行的阶段同时返回 `completed_items` 与 `total_items`。只有最终数据库提交成功后才到 100%。失败或取消保留最后百分比；重试会增加 `attempt` 并把进度归零。running 最多每 5 秒落库一次，同一 finalizing 子阶段最多每秒一次，子阶段变化立即写入。历史 progress 由 API 自动归一化，不需要迁移数据。

## 增量 API

- `GET /api/backtests/{id}/summary`
- `GET /api/backtests/tasks`
- `GET /api/backtests/worker-status`
- `GET /api/backtests/{id}/equity?max_points=1500&shape=chart`
- `GET /api/backtests/{id}/comparison-curves?max_points=1500`
- `GET /api/backtests/{id}/signals?limit=100&cursor=...&symbol=...`
- `GET /api/backtests/{id}/transactions?limit=100&cursor=...&symbol=...`
- `POST /api/backtests/{id}/cancel`

`shape` 默认为 `snapshot`，保持旧调用方兼容，并保留原有全行读取/Python 降采样路径作为回滚选项。`shape=chart` 只返回 `ts`、`equity`、`drawdown` 和可空 benchmark 值，不传输 positions 或完整 metrics 文档。对于紧凑形态，PostgreSQL 在返回前按现有的确定性 first/last 加 bucket min/max 规则选点，包括奇数、偶数和极小 `max_points`。比较曲线接口返回缓存的 SPY/QQQ 点；旧 run 缺失曲线时，从快照与复权历史行情只读重建，不提交数据库改动，并使用同样保留首尾点的数量上限。快照持久化内容和默认 `max_points=1500` 不变。

旧详情接口保留一个迁移周期。列表接口只返回标量指标。Signal 使用 `(ts, symbol, id)` 升序，transaction 使用 `(ts, id)` 降序；cursor 是不透明值。

## 图表加载与渲染

列表页每 4 秒轮询 active run，并显示复用的无障碍百分比进度条。详情页在 queued/running 时只轮询 summary 和 worker status，不请求权益、比较曲线、信号或交易；只有进入 `completed` 后才独立加载这些 payload，并分别保留 error 状态。比较曲线加载失败时仍保留策略曲线，并显示非阻断警告。failed/cancelled 会停止轮询并保留终态进度。manager 自动执行不可用时，列表和详情会优先提示“仍可排队，但执行暂停”；可用时则显示 active/configured 进程数、可用槽位和排队任务数。

回测完成后，详情页先读取最新 100 笔成交，让下方明细尽快可用；随后沿不透明 transaction cursor 每批最多读取 500 笔，直到总览图与个股盈亏覆盖整个 run。页面显示“已加载/总数”，尾部分页失败时保留已有标记，并可从失败 cursor 继续。权益图中的信号与成交标记继续保留形状、颜色、筛选、计数和悬浮详情，但绘图区不再重复显示 `BUY`/`SELL` 文字。成交表初始渲染最新 10 笔，每次增加 10 笔；生命周期初始只基于最新 100 笔成交并显示 12 段，需要时每次把计算范围扩大 100 笔并再显示 12 段。由于生命周期口径有意保持局部，界面始终显示其当前使用的成交数与 run 总成交数。

权益、生命周期和全局股票图表统一使用固定版本 `lightweight-charts@5.2.1` 的客户端模块。模块仅在图表可见时动态加载，不进入 shared 首屏 chunk。平移、滚轮及触控缩放、十字光标和 resize 使用原生 Canvas 交互。Canvas primitives 保留岛形缺口、双底、支撑/压力区域、连接线及标签避让。面板关闭或切换时会销毁图表实例和附加 primitive。TradingView 内置 attribution logo 保持开启。

渲染层变化不改变回测计算、T+1 成交、费用、公司行动、持仓、指标、快照持久化、paper trading 或 worker/scheduler 行为。

## 数据库上线与恢复

项目没有 Alembic。apply 前必须确认并记录准确目标数据库，执行数据库原生备份，检查已有约束/索引，并通过 `ON_ERROR_STOP` 执行 SQL。脚本增加 `backtest_jobs`、`backtest_worker_managers`、可空 `instrument_id` 列和可空 `experiment_trials.cancel_requested_at`，以及所需外键和队列/manager 索引；取消标记无需历史回填。脚本不会预先增加 signals/transactions cursor 索引，也不会重写历史明细，旧 run 默认解释为 `full`。

如果上线失败，先停止新 job 生产者和 backtest worker，再把新任务路由回 v1。应用回滚期间保留附加对象。若以后确实需要删除 schema，必须先证明所有应用版本都不再引用，并在任何完整性异常时从备份恢复；正常发布不执行破坏性回滚。

## 指标与验收

`summary_metrics.performance` 使用互不重叠的阶段记录 `sql_execute_ms`、`sql_fetch_ms`、`row_decode_ms`、`day_grouping_ms`、历史状态、信号、成交、明细构造、明细/摘要持久化、响应构造和总耗时。它同时记录 rows/day/signals/trades 每秒、每输入行微秒数、阶段占比、`unaccounted_ms` 与峰值 RSS；worker 终态补充 queue wait、active 和 finalization overhead。支撑/压力区子阶段只作为诊断维度，不重复计入 `unaccounted_ms`。结构化日志记录同一映射。

研究 trial 使用 experiment `run_manifest.preparedDataset` 中的稳定 key/manifest。v3 key 包含 loader revision、源数据指纹、稳定 instrument 集合、完整日期范围、feature set、价格/公司行动、symbol identity 和 universe membership 语义，不包含普通策略参数。首个 trial 在文件锁内原子构建相互独立且按 Fortran 顺序存储的 `int64` identity/date memmap 与 `float64` feature memmap，并写入 symbol/asset/exchange、日期 offset 和公司行动 sidecar；数值缺失统一使用 NaN。后续 trial 只读打开两个 buffer。v2 key 不会匹配 v3，旧文件也不会被自动删除。文件或 metadata 损坏会在锁内重建；缓存基础设施失败会记录 fallback 原因并回到相同指纹的 DB loader；构建期间行数变化会把 experiment 标为 `data_changed`。手动回测不使用该缓存。cleanup 会统计引用同一 key 的 queued/running job，存在 active lease 时拒绝删除。

原生包使用 C++20、`pybind11==3.1.0`、`-O3` 和 `-DNDEBUG` 构建，并明确禁用 fast-math。本地开发执行 `.venv/bin/pip install -e backend/native`；Docker 在 Linux builder stage 生成 wheel，runtime stage 只安装该 wheel。

原生 `run_backtest(dataset, strategy, options, control_callback)` 基础目前覆盖 Trend、Mean Reversion 和 Momentum Breakout。它零拷贝读取 PreparedDataset v3 的两个 NumPy buffer，在交易循环中释放 GIL，并且每个已完成 session 只为取消/进度回调重新获取一次。typed `KernelResult` 为 signals、trades、equity、positions 及逐 session 动态股票池诊断提供只读 NumPy views。共享原生账本已经保留 T 日收盘决策/T+1 开盘成交、SELL-first、共享现金、确定性强度排名与阈值、仓位上限、最低佣金、不利滑点、拆股数量与成本基准调整、稳定 instrument identity、缺失开盘价和退市写零语义。时点成员资格采用与 Python 一致的确定性排除顺序和逐 instrument 已处理 session 历史计数，只过滤 BUY，并允许不再 eligible 的已有持仓继续退出；三个策略账本和动态股票池 eligibility 均有 Python/C++ 差分。生产 worker 尚未调用该入口：分阶段形态、完整 signal metadata/audit vectors 与 typed persistence 必须分别通过差分后，才执行一次性切换。

支撑/压力策略现在以原生模块作为 detector cache identity、完整 Pivot zone identity、`NUMERIC(24,10)` half-up 价格规范化、确定性加权 Theil-Sen 拟合、冻结 zone 投影校验，以及跨交易日 candidate、regime、入场通道、退出和审计演进的唯一实现。因此，原生公共 catalog 和日线 evaluator 已覆盖全部九个 engine-ready 策略。现有 Python dataclass 暂时保留为传输与持久化结构，原生状态机在原对象上更新；将该边界替换为 typed native state 并在计算时释放 GIL，仍属于后续内核接入工作。已迁移行为不提供运行时引擎选择或 Python 算法 fallback。

只读基准预检：

```bash
make benchmark-backtests BENCHMARK_ARGS="plan"
make benchmark-backtests BENCHMARK_ARGS="screening"
```

`plan` 与没有 `--apply` 的 correctness/screening/confirmation 只输出目标数据库、代码/依赖版本、case、数据指纹和预计 run 数。写入模式必须由操作者显式增加 `--apply`，并要求干净 worktree、零 queued/running job、`RESEARCH_WORKER_ENABLED=false`、两项 paper scheduler 配置为 `false` 且 `BACKTEST_WORKER_CONCURRENCY=1`。每个 case 预热一次并正式运行五次，以 median 为主并保留最大值和全部 run ID。不要在未报告 `hzy/public`、指纹与预计 run 数并取得明确授权时运行写入模式。

三个无状态候选 kernel 已分别实现并复用共享 strategy handler 生成最终事件。默认执行路径暂不启用；screening 会为每个策略和持久化级别生成 baseline/kernel A/B case，只有全市场 confirmation 达到 20% 门槛且差分结果完全一致后，才在后续改动中切换对应策略。

全市场验收门槛是规范化 zone/event 内容完全一致、支撑/压力区收尾耗时至少下降 50%，且峰值 RSS 不高于已记录的 16.1 GB 基线；不得仅凭单元测试宣称通过。手动启用 v2 前仍需跑完 100/500/3,640 股票以及 1 年/5 年/全历史矩阵。索引或 LAG 查询必须由真实 `EXPLAIN ANALYZE` 证明至少改善 20%，不能凭合成测试部署。

当前不依赖 Numba。并发保持 `1|2`；concurrency=4、SQL/索引、ring buffer 和更多 pattern 优化都必须先满足本文的真实观测门槛并另立改动。

图表发布需记录生产构建的 shared 与回测详情 First Load JS，并确认 Lightweight Charts 使用独立 chunk。固定 fixture 使用 1,500/5,000 个权益点、200 个事件以及 500 根 K 线/100 个 marker；在同一生产 Chromium 连续测量 5 次，要求数据就绪到 chart-ready 中位数不超过 100 ms、平移缩放平均不低于 55 FPS，且图表主线程任务不超过 50 ms。

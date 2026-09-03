# 回测性能与 Worker 运维

[English](backtest-performance.md)

## 原生执行边界

九个 engine-ready 日线多头策略统一使用一个进程内 C++20 内核完成完整回测和 Paper Trading 日信号评估。信号规则、T 日收盘决策、下一有效交易日（T+1）开盘成交、先卖后买、共享现金、成本、公司行动、动态股票池、持仓上限和确定性排序仍是不变量。Python 负责数据访问、durable queue、数据库事务、进度/取消、券商副作用和结果持久化；`custom` 继续 stored-only。

系统不再提供运行时引擎选择、job 级 engine version 或 Python 执行回退。新回测摘要写入 `kernel.version=cpp-v1`、ABI、build ID、PreparedDataset schema `v4` 和策略算法 revision；历史记录继续可读。运维回滚是重新部署上一个已验证的应用/wheel 构建，不是在当前进程内选择旧引擎。

## 持久化级别

- `summary`：精确指标和最多 1,500 个确定性 min/max bucket 权益点；不保存 run signals、transactions 或完整 positions。
- `trades`：summary 加 transactions；不保存 signals 和每日完整 positions。
- `full`：保存 signals、transactions、每日 positions/snapshots 和 Support/Resistance run 审计事件。

手动请求默认 `full`，研究 trial 固定请求 `summary`。客户端使用 `persist_level` 与 `available_details` 区分“没有持久化”和真实零记录，禁止把缺失明细显示成零信号或零交易。

手动回测表单始终显式提交选择值，并提供“完整审计 full”“成交分析 trades”“快速摘要 summary”三个中英文选项。改变持久化级别只改变保存的明细，不改变信号、成交、权益或摘要指标的计算。

signals、transactions、snapshots、支撑/压力 zone/regime 版本与运行审计事件，统一使用当前 SQLAlchemy Session 的 `postgresql+psycopg` connection，以每批 5,000 行执行 `COPY FROM STDIN`。首个明细写入前会完整校验 typed 列、枚举、JSON、instrument 引用、有限数、数据库数值边界和支撑/压力几何；每个 COPY 批次前再次检查取消。批次不会独立提交；校验、COPY、取消或物化失败都会回滚该运行的整套明细。缓存命中会跳过共享 zone/regime 写入，但 `full` 仍保存该运行请求的精确事件集合。

候选 promotion 会先创建 OOS/base-cost `full` verification job。job 以相对和绝对 `1e-10` 容差比较数值摘要指标；指标不一致会记录 verification 失败并阻止 promotion。verification run ID 同时写入 candidate metrics 和晋升策略 lineage。

## 按需持久化 Worker

路由流量前先显式应用附加 schema：

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/utils/create_zzzzzzz_backtest_performance.sql
make backtest-worker-manager
```

API 在同一事务创建 `strategy_runs` 与 `backtest_jobs`，即使自动执行不可用也返回 HTTP 201。轻量 manager 常驻，每 2 秒轮询并恢复过期 lease；它持有 PostgreSQL advisory lock，保证只有一个 leader 能启动 worker 组。存在 eligible queued 任务时，leader 启动 `backtest_worker --once --concurrency N`；worker 协调进程通过 multiprocessing `spawn` 上下文创建 `ProcessPoolExecutor`，连续清空队列、等待全部 active job 后退出。每个子进程都会重新导入应用并创建自己的 SQLAlchemy engine/session 状态，不继承协调进程的数据库连接。普通任务异常只影响对应 job；进程池损坏会让 worker 非零退出，再由 manager 现有的 1/2/5/10/30 秒封顶退避和过期 lease 恢复接管。

`BACKTEST_WORKER_CONCURRENCY` 默认为 `2`，只接受 `1` 或 `2`。`BACKTEST_INTRA_RUN_THREADS` 默认为 `4`，接受 `1` 到 `16`；设为 `1` 可立即回退单 run 串行。有效线程数为 `min(配置线程数, max(1, 可用 CPU 数 / worker 进程数))`。Linux 优先使用 affinity/cgroup 可见 CPU 集，其他平台回退到 `os.cpu_count()`。非整数或越界值会让启动明确失败。Docker Compose 会把两个设置同时注入 backend 与 manager，Make 目标继承当前 shell 环境变量。配置只在进程启动时读取，不支持热更新，因此修改后必须同时重启 backend 和 manager。状态接口除进程容量外，还返回 `intra_run_execution_model=thread`、配置线程数和有效线程数。

每个回测统一占用一个进程槽。单个 run 内交易日之间保持串行，但原生内核通过一个可复用的 C++20 固定线程池并行评估同日不同标的。公司行动、退市、上一日信号成交、SELL-first、共享现金、持仓上限、确定性 BUY 排名、审计序列化和权益计算仍为串行。当日少于 `64 × 有效线程数` 行时自动串行；如果 warmup 与正式交易日都未达到阈值，则完全不创建工作线程。两个全市场或长周期任务可能接近单任务两倍的内存压力；若 CPU 限额配置不准确，“进程数 × run 内线程数”还可能导致过度并行。应观测 peak RSS 与 CPU 饱和度：需要单 run 回退时先降低 `BACKTEST_INTRA_RUN_THREADS`，内存紧张时再把进程并发降到 `1`。全局只应由 advisory-lock leader manager 启动这一组 worker；自动 manager 运行时不要额外执行 `make backtest-worker` 或其他诊断 worker，否则会突破配置的全局槽位数。

`backtest_worker_managers` 中的 manager 每 5 秒心跳。leader 心跳超过 15 秒即视为自动执行不可用。空队列时，只要 manager 健康，即使没有 worker 子进程，平台仍为 ready。`make dev`、`make dev-agent-safe` 和 `make backtest-worker-manager` 会监管 manager，并在意外退出 2 秒后自动重启；`make dev-agent-all` 与 Docker Compose 使用各自的进程监管。所有完整启动路径都会强制关闭两项 paper scheduler 配置。`make dev-backend`、`make dev-frontend` 和直接运行 `uvicorn` 属于部分启动方式，不保证自动消费队列。`make backtest-worker` 仍保留为显式诊断/运维命令。

worker 使用 `FOR UPDATE SKIP LOCKED` claim，维护 heartbeat/lease，每个交易日检查取消，重试前只清理当前 run 的未完成明细，并在重试耗尽时同步结束关联 research trial。

## 统一任务中心

`/backtest-tasks` 是现有研究调度、`strategy_runs`、`backtest_jobs` 和 verification candidate 的只读投影。`GET /api/backtests/tasks` 合并手动回测、每个研究 trial（包括尚未进入回测队列的 trial）和候选验证任务；已经关联 run/job 的 trial 仍只出现一行。活动任务排在终态历史之前，来源/阶段筛选和 25/50/100 条分页均由服务端执行。

页面有意并列展示两层健康状态。`GET /api/research/worker-status` 解释 trial 是否仍在等待研究调度；`GET /api/backtests/worker-status` 解释已经入队的 run 是否具备执行容量。仅当当前结果页含有非终态任务时，任务列表才每 4 秒轮询。取消继续使用现有协作式回测安全边界。失败的普通回测或验证回测可通过 `POST /api/backtests/{runId}/retry` 重试一次；重试会依据相同的 durable payload 创建新 run/job，并保留原失败记录用于审计。普通回测的 completed、failed 和 cancelled 终态任务可通过现有 `DELETE /api/backtests/{runId}` 边界逐条删除，删除不会沿重试链级联。研究 trial 与验证证据仍由所属实验工作流管理，只能从实验页删除。任务中心不会创建第二套队列，也不改变信号、成交、成本或持久化语义。

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

`shape` 默认为 `snapshot`，保持旧调用方兼容，并保留原有全行读取/Python 降采样路径作为回滚选项。`shape=chart` 只返回 `ts`、`equity`、`drawdown` 和可空 benchmark 值，不传输 positions 或完整 metrics 文档。对于紧凑形态，PostgreSQL 在返回前按现有的确定性 first/last 加 bucket min/max 规则选点，包括奇数、偶数和极小 `max_points`。比较曲线接口返回与市场匹配的缓存点；A 股回测使用上证指数和深证成指，其他回测使用 SPY 和 QQQ。旧 run 缺失曲线时，从快照与复权历史行情只读重建，不提交数据库改动，并使用同样保留首尾点的数量上限。快照持久化内容和默认 `max_points=1500` 不变。

旧详情接口保留一个迁移周期。列表接口只返回标量指标。Signal 使用 `(ts, symbol, id)` 升序，transaction 使用 `(ts, id)` 降序；cursor 是不透明值。

## 图表加载与渲染

列表页每 4 秒轮询 active run，并显示复用的无障碍百分比进度条。详情页在 queued/running 时只轮询 summary 和 worker status，不请求权益、比较曲线、信号或交易；只有进入 `completed` 后才独立加载这些 payload，并分别保留 error 状态。比较曲线加载失败时仍保留策略曲线，并显示非阻断警告。failed/cancelled 会停止轮询并保留终态进度。manager 自动执行不可用时，列表和详情会优先提示“仍可排队，但执行暂停”；可用时则显示 active/configured 进程数、可用槽位和排队任务数。

回测完成后，详情页先读取最新 100 笔成交，让下方明细尽快可用；随后沿不透明 transaction cursor 每批最多读取 500 笔，直到总览图与个股盈亏覆盖整个 run。页面显示“已加载/总数”，尾部分页失败时保留已有标记，并可从失败 cursor 继续。权益图中的信号与成交标记继续保留形状、颜色、筛选、计数和悬浮详情，但绘图区不再重复显示 `BUY`/`SELL` 文字。成交表初始渲染最新 10 笔，每次增加 10 笔；生命周期初始只基于最新 100 笔成交并显示 12 段，需要时每次把计算范围扩大 100 笔并再显示 12 段。由于生命周期口径有意保持局部，界面始终显示其当前使用的成交数与 run 总成交数。

权益、生命周期和全局股票图表统一使用固定版本 `lightweight-charts@5.2.1` 的客户端模块。模块仅在图表可见时动态加载，不进入 shared 首屏 chunk。平移、滚轮及触控缩放、十字光标和 resize 使用原生 Canvas 交互。Canvas primitives 保留岛形缺口、双底、支撑/压力区域、连接线及标签避让。面板关闭或切换时会销毁图表实例和附加 primitive。TradingView 内置 attribution logo 保持开启。

渲染层变化不改变回测计算、下一有效交易日（T+1）开盘成交、费用、公司行动、持仓、指标、快照持久化、paper trading 或 worker/scheduler 行为。

## 数据库上线与恢复

项目没有 Alembic。apply 前必须确认并记录准确目标数据库，执行数据库原生备份，检查已有约束/索引，并通过 `ON_ERROR_STOP` 执行 SQL。新安装由 `create_zzzzzzz_backtest_performance.sql` 创建 `market_data_maintenance_state`；现有安装使用已审阅的增量脚本 `backend/utils/remove_market_data_fingerprints.sql`，它会移除指纹字段和 `data_changed`、统一失效现有支撑/压力物化但保留历史链接，并创建“当前记录”部分唯一索引。该 SQL 永远不会自动应用。脚本不会预先增加 signals/transactions cursor 索引，也不会重写历史运行明细。

如果上线失败，先停止新 job 生产者和 backtest worker，再重新部署上一个已验证的应用和原生 wheel。应用回滚期间保留附加对象。若以后确实需要删除 schema，必须先证明所有应用版本都不再引用，并在任何完整性异常时从备份恢复；正常发布不执行破坏性回滚。

## 指标与验收

`summary_metrics.performance` 使用互不重叠的阶段记录 `sql_execute_ms`、`sql_fetch_ms`、`row_decode_ms`、`day_grouping_ms`、历史状态、信号、成交、明细构造、明细/摘要持久化、响应构造和总耗时。它同时记录 rows/day/signals/trades 每秒、每输入行微秒数、阶段占比、`unaccounted_ms`、峰值 RSS、配置/有效/实际 run 内线程数、并行/串行交易日数、原生 warmup 耗时和正式信号生成耗时；worker 终态补充 queue wait、active 和 finalization overhead。支撑/压力区子阶段只作为诊断维度，不重复计入 `unaccounted_ms`。结构化日志记录同一映射。

手动、研究和验证回测都使用 `run_manifest.preparedDataset` 中的稳定 key/manifest。v4 key 包含 loader/schema revision、稳定 instrument 集合、股票池规则、覆盖与请求日期范围、feature set、价格/公司行动、symbol identity 和 universe membership 语义，不包含普通策略参数或行情逐行摘要。热缓存会在任何源行查询前直接只读打开 memmap。冷缓存或损坏缓存先执行一次行数统计，再在文件锁内流式、原子构建相互独立且按 Fortran 顺序存储的 `int64` identity/date memmap 与 `float64` feature memmap，并写入 symbol/asset/exchange、日期 offset、公司行动和动态 universe sidecar；数值缺失统一使用 NaN。cleanup 会统计引用同一 key 的 queued/running job，存在 active lease 时拒绝删除。

## 排他行情维护

每日 20:15 行情更新必须通过 `run_daily_market_backfill.py` 执行。写入运行会把单例状态从 `ready` 切到 `draining`，以 HTTP 409 `market_data_maintenance` 拒绝新的普通回测、重试、研究和验证，并等待所有 queued/running job 及全部非终态研究实验结束；已有 job 继续执行。进入 draining 后不再启动新的 Paper 策略计算，已经持有共享 advisory lock 的工作可安全完成。

排空后，更新器取得 PostgreSQL 排他 advisory lock，把所有当前支撑/压力物化标记为失效，删除生成的 PreparedDataset 文件，再开始写源表。子脚本接收 `MARKET_DATA_MAINTENANCE_OWNER`，属于同一个父维护窗口。只有流水线和质量门禁都成功后才恢复 `ready`；任何失败都会进入 `failed`，之后回测、研究、验证和 Paper 计算持续阻塞，直到下一次维护重跑成功。绕过协调器直接写源表或单独运行写入子脚本属于不支持的操作，因为可能留下陈旧派生缓存。

原生包使用 C++20、`pybind11==3.1.0`、`-O3`、`-DNDEBUG` 和平台标准线程编译/链接参数构建，并明确禁用 fast-math。ABI 要求为 `2`，`KERNEL_VERSION` 仍为 `cpp-v1`，因为并行执行没有改变交易算法或结果语义。本地开发执行 `.venv/bin/pip install -e backend/native`；Docker 在 Linux builder stage 生成 wheel，runtime stage 只安装该 wheel。应用启动时会拒绝 ABI 不匹配的 wheel。

原生 `run_backtest(dataset, strategy, options, control_callback)` 覆盖 Trend、Mean Reversion、Momentum Breakout、Island Reversal、Double Bottom、Head-and-Shoulders Bottom、Rounded Bottom、V Reversal 和 Support/Resistance。内部 `options.thread_count` 对直接原生调用方默认 `1`。它零拷贝读取 PreparedDataset v4 NumPy buffer，在 warmup 与正式 session 复用同一个固定线程池，并在日期之间设置 barrier；每个正式 session 只调用一次 Python control callback；`start_date` 之前的行只预热有界历史、形态/支撑压力状态和动态股票池计数，不生成 signal、trade、equity 行或回调。Pattern 与 Support/Resistance 状态在线程启动前按 instrument 初始化，每个线程只修改自己标的的状态并写入独立结果槽；多行异常按最低输入行确定性重抛。typed `KernelResult` 为 signals、trades、equity/positions、universe diagnostics 和支撑/压力审计 vector 提供只读 NumPy view 与 canonical JSON 列，并提供只读线程/session/耗时性能数据。共享原生账本保留 T 日收盘/T+1 开盘、SELL-first、共享现金、确定性强度排序与阈值、仓位上限、分阶段目标、最低佣金、不利滑点、公司行动、稳定 identity、缺失开盘、动态股票池 exit-only 和退市归零。

Paper 日信号会把有界历史和当前组合状态转换为内存 v4 列式视图，再调用 `evaluate_day(dataset_day, strategy, portfolio_state)`。回归测试要求其 action、顺序、reason、score 和 canonical metadata 与对应原生回测 session 完全一致。

冻结 golden fixture 保存九个策略切换前的 oracle 和纯 STL 重构前完整账本结果。`native_nine_strategy_golden.json` 对固定 `20 symbols × 120 sessions` 矩阵中的全部 typed signals/trades/equity/positions/audit vector 计算指纹，并记录用于恢复 oracle 的准确 base commit 与 diff digest。原生测试还按 `1e-10` 数值容差覆盖逐日排序、原因、metadata、分阶段审计和支撑/压力生命周期证据；Paper 测试会在全部 mock Alpaca 的前提下比较日评估与同日回测信号契约。这些检查是语义回归证据，不等于数据库规模性能门槛。

支撑/压力使用不包含 Python 头文件的 `support_resistance_core.hpp/.cpp` 纯 STL 状态机实现 cache identity、完整 Pivot zone identity、`1e-10` half-up 价格规范化、确定性加权 Theil-Sen 拟合、冻结几何、四状态演进、pending outcome、posterior 证据、入场通道、退出和 typed 审计。`support_resistance_kernel.cpp` 只在释放 GIL 前转换 Python 边界 DTO，并在计算结束后转换 typed 结果。Python 只查询并锁定缓存表、hydrate 原生输入，并把最终 typed vector 适配为持久化行；不存在另一套 detector 或交易状态机。`run_backtest` 的 S/R warmup 和正式 session 计算全程无 GIL，唯一计算期获取点是每个正式 session 一次的 control callback。

只读基准预检：

```bash
make benchmark-backtests BENCHMARK_ARGS="plan"
```

`plan` 会报告目标数据库、原生 ABI/build、case、服务状态和完整写入授权范围，但不创建 `StrategyRun`。固定完整矩阵包含 1 个 benchmark-only Draft Strategy、105 个冻结 Python baseline run 和 159 个 native correctness/performance run（合计 264 个 `StrategyRun`）；`currentlyRunnableNativeRunCount` 会另外暴露缺失策略 fixture，不能因缺失 fixture 静默缩小授权范围。correctness、screening 和 confirmation 必须显式增加 `--apply`，且工作区干净、无 queued/running job、`RESEARCH_WORKER_ENABLED=false`、两项 paper scheduler 配置为 `false`、`BACKTEST_WORKER_CONCURRENCY=1`。screening 和 confirmation 还必须提供 `--baseline /path/to/frozen-python-report.json`；命令会计算每项速度与 RSS 门槛，缺少任一 baseline case 都会阻断验收。每个测量 case 先预热一次，再正式运行五次，并保留每个 run ID。在报告准确数据库、服务状态和预计 run 数并取得明确授权前，不得执行写入模式。

screening 覆盖九策略 `500 symbols × 1 year` warm `summary`，要求原生中位速度至少为冻结 Python 基线的 `5×`。confirmation 覆盖 Trend、Double Bottom 和 Support/Resistance `3,640 symbols × 5 years`：cold `summary ≥3×`、warm `summary ≥5×`、warm `full ≥2×`，且峰值 RSS 不高于基线。cold 包含数据库读取与 v4 缓存构建，warm 从相同结构缓存开始。若 cold 未达到 `3×`，下一实现步骤是 PostgreSQL COPY 流式构建列式缓存，不能降低门槛。

不能凭单元测试或 synthetic smoke 宣称通过性能门槛。只有另行授权的数据库规模矩阵全部达标，候选才可部署。索引或 SQL 查询改动仍必须有真实 `EXPLAIN ANALYZE` 证据，不能从 synthetic 测试推断改善。

当前不依赖 Numba。外层进程并发保持 `1|2`，run 内线程限制为 `1..16` 并受可用 CPU 自动限额。把外层并发提高到 `2` 以上、SQL/索引、ring buffer 和更多 pattern 优化都必须先满足本文的真实观测门槛并另立改动。

图表发布需记录生产构建的 shared 与回测详情 First Load JS，并确认 Lightweight Charts 使用独立 chunk。固定 fixture 使用 1,500/5,000 个权益点、200 个事件以及 500 根 K 线/100 个 marker；在同一生产 Chromium 连续测量 5 次，要求数据就绪到 chart-ready 中位数不超过 100 ms、平移缩放平均不低于 55 FPS，且图表主线程任务不超过 50 ms。

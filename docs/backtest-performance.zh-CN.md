# 回测性能与 Worker 运维

[English](backtest-performance.md)

## 原生执行边界

九个 engine-ready 日线多头策略统一使用一个进程内 C++20 内核完成完整回测和 Paper Trading 日信号评估。信号规则、T 日收盘决策、下一有效交易日（T+1）开盘成交、先卖后买、共享现金、成本、公司行动、动态股票池、持仓上限和确定性排序仍是不变量。Python 负责数据访问、durable queue、数据库事务、进度/取消、券商副作用和结果持久化；`custom` 继续 stored-only。

系统不再提供运行时引擎选择、job 级 engine version 或 Python 执行回退。新回测摘要写入 `kernel.version=cpp-v1`、ABI、build ID、PreparedDataset schema `v5` 和策略算法 revision；历史记录继续可读。运维回滚是重新部署上一个已验证的应用/wheel 构建，不是在当前进程内选择旧引擎。

## 持久化级别

- `summary`：精确指标和最多 1,500 个确定性 min/max bucket 权益点；不保存 run signals、transactions 或完整 positions。
- `trades`：summary 加 transactions；不保存 signals 和每日完整 positions。
- `full`：保存 signals、transactions、每日 positions/snapshots 和 Support/Resistance run 审计事件。

手动请求默认 `full`，研究 trial 固定请求 `summary`。客户端使用 `persist_level` 与 `available_details` 区分“没有持久化”和真实零记录，禁止把缺失明细显示成零信号或零交易。

手动回测表单始终显式提交选择值，并提供“完整审计 full”“成交分析 trades”“快速摘要 summary”三个中英文选项。改变持久化级别只改变保存的明细，不改变信号、成交、权益或摘要指标的计算。

signals、transactions、snapshots、支撑/压力 zone/regime 版本、共享生命周期事件与运行决策事件，统一使用当前 SQLAlchemy Session 的 `postgresql+psycopg` connection，以每批 5,000 行执行 `COPY FROM STDIN`。原生审计输出直接提供 typed 查询列和每个事件唯一生成的 canonical JSON；Python 只做一次严格边界校验，再把原 JSON 交给 COPY。批次不会独立提交；校验、COPY、取消、物化、链接或摘要失败都会回滚该运行的完整结果。审计格式 v2 把 `touch`、`invalidation`、`phase_ended`、`regime_transition`、`entry_channel_started` 和 `entry_channel_ended` 按物化只保存一次。缓存命中跳过 zone、regime 与共享事件的遍历/校验，只写运行决策事件。旧物化继续服务历史运行，但不能命中新版缓存。

候选 promotion 会先创建 OOS/base-cost `full` verification job。job 以相对和绝对 `1e-10` 容差比较数值摘要指标；指标不一致会记录 verification 失败并阻止 promotion。verification run ID 同时写入 candidate metrics 和晋升策略 lineage。

## 按需持久化 Worker

路由流量前先显式应用附加 schema：

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/utils/create_zzzzzz_support_resistance.sql
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

进度阶段固定为 `queued`（0%）、`preparing`（0%）、`running`（已完成交易日映射到 0–85%）、`finalizing`（85–99%）以及终态 `completed`、`failed`、`cancelled`。finalizing 会进一步报告 `zone_versions`、`regime_versions`、`materialization_events`、`run_events`、`backtest_details` 或 `committing`；按条目执行的阶段同时返回 `completed_items` 与 `total_items`。只有最终数据库提交成功后才到 100%。失败或取消保留最后百分比；重试会增加 `attempt` 并把进度归零。running 最多每 5 秒落库一次，同一 finalizing 子阶段最多每秒一次，子阶段变化立即写入。历史 progress 由 API 自动归一化，不需要迁移数据。

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

列表页每 4 秒轮询 active run，并显示复用的无障碍百分比进度条。详情页不显示顶部执行进度面板，执行进度可在列表页或任务中心查看。回测概览中的“最新快照”默认收起，展开后显示时间、现金、权益和回撤。详情页在 queued/running 时只轮询 summary 和 worker status，不请求权益、比较曲线、信号或交易；只有进入 `completed` 后才独立加载这些 payload，并分别保留 error 状态。比较曲线加载失败时仍保留策略曲线，并显示非阻断警告。failed/cancelled 会停止轮询并保留终态进度。manager 自动执行不可用时，列表和详情会优先提示“仍可排队，但执行暂停”；可用时则显示 active/configured 进程数、可用槽位和排队任务数。

回测完成后，详情页先读取最新 100 笔成交，让下方明细尽快可用；随后沿不透明 transaction cursor 每批最多读取 500 笔，直到总览图与个股盈亏覆盖整个 run。页面显示“已加载/总数”，尾部分页失败时保留已有标记，并可从失败 cursor 继续。权益图中的信号与成交标记继续保留形状、颜色、筛选、计数和悬浮详情，但绘图区不再重复显示 `BUY`/`SELL` 文字。成交表初始渲染最新 10 笔，每次增加 10 笔；生命周期初始只基于最新 100 笔成交并显示 12 段，需要时每次把计算范围扩大 100 笔并再显示 12 段。由于生命周期口径有意保持局部，界面始终显示其当前使用的成交数与 run 总成交数。

权益、生命周期和全局股票图表统一使用固定版本 `lightweight-charts@5.2.1` 的客户端模块。模块仅在图表可见时动态加载，不进入 shared 首屏 chunk。平移、滚轮及触控缩放、十字光标和 resize 使用原生 Canvas 交互。Canvas primitives 保留岛形缺口、双底、支撑/压力区域、连接线及标签避让。面板关闭或切换时会销毁图表实例和附加 primitive。TradingView 内置 attribution logo 保持开启。

渲染层变化不改变回测计算、下一有效交易日（T+1）开盘成交、费用、公司行动、持仓、指标、快照持久化、paper trading 或 worker/scheduler 行为。

## 数据库上线与恢复

项目没有 Alembic。apply 前必须确认并记录准确目标数据库，执行数据库原生备份，检查已有约束/索引，并通过 `ON_ERROR_STOP` 执行 SQL。`create_zzzzzz_support_resistance.sql` 增加审计格式版本与 `support_resistance_materialization_events`；既有物化标记为版本 1，不改写历史事件，新计算使用版本 2。新安装由 `create_zzzzzzz_backtest_performance.sql` 创建 `market_data_maintenance_state`；现有安装使用已审阅的增量脚本 `backend/utils/remove_market_data_fingerprints.sql`，它会移除指纹字段和 `data_changed`、统一失效现有支撑/压力物化但保留历史链接，并创建“当前记录”部分唯一索引。SQL 永远不会自动应用。脚本不会预先增加 signals/transactions cursor 索引，也不会重写历史运行明细。

如果上线失败，先停止新 job 生产者和 backtest worker，再重新部署上一个已验证的应用和原生 wheel。应用回滚期间保留附加对象。若以后确实需要删除 schema，必须先证明所有应用版本都不再引用，并在任何完整性异常时从备份恢复；正常发布不执行破坏性回滚。

## 指标与验收

已完成回测的 summary/detail 接口在 `summary_metrics.performance` 返回墙钟毫秒。新读取链路使用下列不重叠的准备阶段；不要把旧版 `sql_fetch_ms` 与新分片阶段直接作逐项等价比较。

| 字段 | 计时范围 |
| --- | --- |
| `sql_read_ms` | instrument/symbol 元数据和公司行动查询；不含分片 COPY。 |
| `shard_load_ms` | 所有分片读取/构建的并行墙钟时间，含 COPY、临时文件、NumPy 解码、分片数组刷盘与发布；不是线程时间之和，也不是网络耗时。 |
| `row_conversion_ms` | 原字典转换指标；新列式路径为零，解码计入分片阶段。 |
| `array_write_ms` | 最终数组分配、分片拼装、按日排序、字典重映射、sidecar 和刷盘。 |
| `first_chunk_ready_ms` / `chunk_load_ms` / `chunk_compute_ms` | 首个可用年度块就绪时间，以及逐块加载/计算墙钟。 |
| `producer_wait_ms` / `consumer_wait_ms` / `pipeline_overlap_ms` | 深度 1 队列的生产者背压、消费者等待，以及加载与计算区间的实际交叠。 |
| `signal_persist_ms` / `transaction_persist_ms` / `snapshot_persist_ms` | 既有 typed 普通明细表的独立写入时间。 |
| `support_resistance_materialization_events` / `support_resistance_run_events` | 新物化创建的共享事件行和本次运行写入的动态事件行。 |
| `native_warmup_ms` | 请求日期之前的策略状态预热；也包含在 `native_kernel_ms` 内，不可重复相加。 |
| `universe_resolution_ms` | 股票池标准化和稳定 instrument identity 解析；独立于上述取数阶段。 |

直接命中最终缓存时加载/拼装阶段为零，`shard_load_ms` 和分片计数可以缺省。股票池解析、锁等待和部分最终元数据工作仍不在上述阶段内；端到端比较以独立墙钟及 `engine_total_ms` 为准。分片数量通过 `shards_hit` / `shards_built` 返回。既有结果不会补写计时字段。原生计算线程、执行时序、成本、P&L 和持久化级别不变。

## 列式读取与分片缓存

手动、研究和验证回测共用单一 COPY 读取实现，输出原生 ABI 3 接受的 v5 Fortran-order int64/float64 数据块。manifest 使用 `read_path_revision=binary-shards-3`，旧缓存不匹配新请求，也不会自动删除。完整的前复权 OHLC 已包含拆股类公司行动，因此账本不会再次调整股数；全程未复权的序列仍由原生账本调整股数和平均成本。若公司行动前混用复权与未复权 OHLC，数据准备会失败，因为两种账本处理都无法保持单一价格口径。

生产者按裁剪后的日历年为完整解析 instrument 集合生成数据块，并送入深度为 1 的队列。首个非空块就绪后消费者立即开始原生计算，同时生产者准备下一块。所有冷块导入同一个 PostgreSQL 导出快照，并继续持有现有行情维护读锁。完成的数据块原子发布并可复用；失败或取消时删除尚未发布的临时目录。空年份不送入原生会话。每块计算后立即解除大数组映射；支撑/压力仅保留持久化校验所需的 instrument、symbol 与 session 日期紧凑列。

读取按稳定的 `instrument_id // 256` 桶和完整日历年分片；分片键含实际 instrument 集合、年份、schema 和读取修订，不含策略或精确请求日期。增加一只股票只改变所属桶各年份的键，同年移动结束日期可直接复用分片。完整年份可能多读窗口外行情，但只按原 coverage 范围拼装输入；每个 instrument 的 LAG 精确补入年初之前最后一条 feature，且在关联 bars 之前计算，缺失 bar 不会跳过 feature 前值。所有价格仍优先前复权，缺失浮点为 NaN，符号区间使用包含边界和 valid_from/id 的原有优先级。

COPY 使用固定 int8/float8 列，显式校验二进制头、列数、长度和结束标记。流式写入临时文件后按实际行数一次分配数组，不执行 COUNT 预飞，不预填全量 NaN，也不截断已分配的 Fortran 数组。NumPy 批量解码后物理重排为 session/instrument 顺序并校验同日身份冲突，再交给未修改的 C++ 内核；date_offsets 只作元数据。

`BACKTEST_READ_WORKERS` 默认 4（1–4），`BACKTEST_READ_WORK_MEM_MB` 默认 128（4–512），按回测冷建读取。并行连接导入同一个 PostgreSQL snapshot，元数据解析使用导出连接；主回测保持现有行情维护共享锁。work_mem 是每节点/每进程预算，Hash 还受 hash_mem_multiplier 影响；多回测并发时应降低读取线程或内存预算，不按连接数简单计算上限。缺少 psycopg3 时沿用启动门禁拒绝执行，不回退旧 loader。

无历史需求的三个无状态 descriptor 在固定股票池下不再加载 400 天原始历史；prev 指标仍由精确边界种子提供。状态策略以及 point-in-time 股票池继续保持 400 个日历日，不能把 descriptor 的交易日数直接当日历日数下调。原计划 C-1 的任意逐策略缩短尚未通过完整状态等价性验证，未开放不安全的热身开关。

最终缓存和分片都在文件锁内原子发布。读取失败只移除本次未发布临时文件；空分片也可缓存。最终缓存 cleanup 仍保护 active lease，共享分片保留给其他窗口复用；每日排他行情维护会一并失效最终缓存和 shards。禁止绕过维护窗口直接更新源数据。此改动不重置数据库、不改 broker 状态；回滚使用此前代码提交，重新构建派生缓存即可。

查询观察使用：

```bash
make explain-feature-query
# 单个真实 instrument 桶/年份，重复 3 次，输出 EXPLAIN JSON 与 COPY+临时文件墙钟
```

该命令只查询源表并生成本地报告，不创建回测。EXPLAIN 与 COPY 的时间分别报告，不相减推断传输成本，不声称 shared read 等于物理冷盘。性能回归必须固定策略、股票池、成本、覆盖窗口、代码版本及数据输入，前后都使用独立空目录；原生 golden 之外还需真实旧/新数组和 sidecar 差分，以及停牌、缺失 bar、改名、首日 NULL 的 PostgreSQL 契约测试。

## 排他行情维护

每日 20:15 行情更新必须通过 `run_daily_market_backfill.py` 执行。写入运行会把单例状态从 `ready` 切到 `draining`，以 HTTP 409 `market_data_maintenance` 拒绝新的普通回测、重试、研究和验证，并等待所有 queued/running job 及全部非终态研究实验结束；已有 job 继续执行。进入 draining 后不再启动新的 Paper 策略计算，已经持有共享 advisory lock 的工作可安全完成。

排空后，更新器取得 PostgreSQL 排他 advisory lock，把所有当前支撑/压力物化标记为失效，删除生成的 PreparedDataset 文件，再开始写源表。子脚本接收 `MARKET_DATA_MAINTENANCE_OWNER`，属于同一个父维护窗口。只有流水线和质量门禁都成功后才恢复 `ready`；任何失败都会进入 `failed`，之后回测、研究、验证和 Paper 计算持续阻塞，直到下一次维护重跑成功。绕过协调器直接写源表或单独运行写入子脚本属于不支持的操作，因为可能留下陈旧派生缓存。

原生包使用 C++20、`pybind11==3.1.0`、`-O3`、`-DNDEBUG` 和平台标准线程编译/链接参数构建，并明确禁用 fast-math。ABI 要求为 `3`，`KERNEL_VERSION` 仍为 `cpp-v1`，因为并行执行没有改变交易算法或结果语义。本地开发执行 `.venv/bin/pip install -e backend/native`；Docker 在 Linux builder stage 生成 wheel，runtime stage 只安装该 wheel。应用启动时会拒绝 ABI 不匹配的 wheel。

原生 `run_backtest(dataset, strategy, options, control_callback)` 覆盖 Trend、Mean Reversion、Momentum Breakout、Island Reversal、Double Bottom、Head-and-Shoulders Bottom、Rounded Bottom、V Reversal 和 Support/Resistance。内部 `options.thread_count` 对直接原生调用方默认 `1`。单次调用只是有状态 `BacktestSession` 的单块包装；`consume(chunk)` 跨块保留现金、持仓、最后价格、下一有效交易日待执行信号、历史计数、形态与支撑/压力状态及全局 session index，`finish()` 统一输出最终账本。它零拷贝读取 PreparedDataset v5 NumPy buffer，在 warmup 与正式 session 复用同一个固定线程池，并在日期之间设置 barrier；每个正式 session 只调用一次 Python control callback；`start_date` 之前的行只预热有界历史、形态/支撑压力状态和动态股票池计数，不生成 signal、trade、equity 行或回调。Pattern 与 Support/Resistance 状态在线程启动前按 instrument 初始化，每个线程只修改自己标的的状态并写入独立结果槽；多行异常按最低输入行确定性重抛。typed `KernelResult` 为 signals、trades、equity/positions、universe diagnostics 和支撑/压力审计 vector 提供只读 NumPy view 与 canonical JSON 列，并提供只读线程/session/耗时性能数据。共享原生账本保留 T 日收盘/T+1 开盘、SELL-first、共享现金、确定性强度排序与阈值、仓位上限、分阶段目标、最低佣金、不利滑点、公司行动、稳定 identity、缺失开盘、动态股票池 exit-only 和退市归零。

Paper 日信号会把有界历史和当前组合状态转换为内存 v5 列式视图，再调用 `evaluate_day(dataset_day, strategy, portfolio_state)`。回归测试要求其 action、顺序、reason、score 和 canonical metadata 与对应原生回测 session 完全一致。

冻结 golden fixture 保存九个策略切换前的 oracle 和纯 STL 重构前完整账本结果。`native_nine_strategy_golden.json` 对固定 `20 symbols × 120 sessions` 矩阵中的全部 typed signals/trades/equity/positions/audit vector 计算指纹，并记录用于恢复 oracle 的准确 base commit 与 diff digest。原生测试还按 `1e-10` 数值容差覆盖逐日排序、原因、metadata、分阶段审计和支撑/压力生命周期证据；Paper 测试会在全部 mock Alpaca 的前提下比较日评估与同日回测信号契约。这些检查是语义回归证据，不等于数据库规模性能门槛。

支撑/压力使用不包含 Python 头文件的 `support_resistance_core.hpp/.cpp` 纯 STL 状态机实现 cache identity、完整 Pivot zone identity、`1e-10` half-up 价格规范化、确定性加权 Theil-Sen 拟合、冻结几何、四状态演进、pending outcome、posterior 证据、入场通道、退出和 typed 审计。`support_resistance_kernel.cpp` 只在释放 GIL 前转换 Python 边界 DTO，并在计算结束后转换 typed 结果。Python 只查询并锁定缓存表、hydrate 原生输入，并把最终 typed vector 适配为持久化行；不存在另一套 detector 或交易状态机。`run_backtest` 的 S/R warmup 和正式 session 计算全程无 GIL，唯一计算期获取点是每个正式 session 一次的 control callback。

### 支撑/压力审计收尾

仅用于输出的事件在每个标的/交易日计算后序列化并释放对象树，保留全部事件及原有 instrument/顺序。JSON 对象编码按引用读取，不再深复制对象。最终审计导出和状态释放均释放 GIL；内部可选的 `finalizing_callback(completed_instruments, total_instruments)` 在标的之间检查取消，不额外调用日级 control callback。心跳线程可以在导出期间继续运行。

原生审计输出提供 typed 事件、日期、zone、score、价格列、物化事件标志，以及每行唯一生成的只读 canonical JSON。持久化只校验被选择的行一次，并把原 JSON 通过现有每批 5,000 行 COPY 流程写入。缓存复用时，原生标志直接选出动态行，不遍历或解码共享生命周期 payload。几何、身份、严格 JSON 校验及事务回滚保持不变。

沿用已有进度结构：原生导出为 85–88%，明细准备为 88%，区域/状态/事件持久化为 90–98%，提交为 99%。导出计数单位是标的，持久化计数单位是该阶段组内的行。写入批次前检查取消。审计格式 v2 是附加 DDL，不批量改写历史事件；按文档的备份与预检流程执行。使用 `.venv/bin/pip install --no-build-isolation --no-deps -e backend/native` 重编译本地 wheel，待活动任务停止后重启回测 manager。已取消的运行保持取消，重编译不会重试它们。为验收启动后端时继续关闭两个 paper scheduler 开关。

只读基准预检：

```bash
make benchmark-backtests BENCHMARK_ARGS="plan"
```

`plan` 会报告目标数据库、原生 ABI/build、case、服务状态和完整写入授权范围，但不创建 `StrategyRun`。固定完整矩阵包含 1 个 benchmark-only Draft Strategy、105 个冻结 Python baseline run 和 159 个 native correctness/performance run（合计 264 个 `StrategyRun`）；`currentlyRunnableNativeRunCount` 会另外暴露缺失策略 fixture，不能因缺失 fixture 静默缩小授权范围。correctness、screening 和 confirmation 必须显式增加 `--apply`，且工作区干净、无 queued/running job、`RESEARCH_WORKER_ENABLED=false`、两项 paper scheduler 配置为 `false`、`BACKTEST_WORKER_CONCURRENCY=1`。screening 和 confirmation 还必须提供 `--baseline /path/to/frozen-python-report.json`；命令会计算每项速度与 RSS 门槛，缺少任一 baseline case 都会阻断验收。每个测量 case 先预热一次，再正式运行五次，并保留每个 run ID。在报告准确数据库、服务状态和预计 run 数并取得明确授权前，不得执行写入模式。

screening 覆盖九策略 `500 symbols × 1 year` warm `summary`，要求原生中位速度至少为冻结 Python 基线的 `5×`。confirmation 覆盖 Trend、Double Bottom 和 Support/Resistance `3,640 symbols × 5 years`：cold `summary ≥3×`、warm `summary ≥5×`、warm `full ≥2×`，且峰值 RSS 不高于基线。cold 包含数据库读取与 v5 缓存构建，warm 从相同结构缓存开始。若 cold 未达到 `3×`，下一实现步骤是 PostgreSQL COPY 流式构建列式缓存，不能降低门槛。

不能凭单元测试或 synthetic smoke 宣称通过性能门槛。只有另行授权的数据库规模矩阵全部达标，候选才可部署。索引或 SQL 查询改动仍必须有真实 `EXPLAIN ANALYZE` 证据，不能从 synthetic 测试推断改善。

当前不依赖 Numba。外层进程并发保持 `1|2`，run 内线程限制为 `1..16` 并受可用 CPU 自动限额。把外层并发提高到 `2` 以上、SQL/索引、ring buffer 和更多 pattern 优化都必须先满足本文的真实观测门槛并另立改动。

图表发布需记录生产构建的 shared 与回测详情 First Load JS，并确认 Lightweight Charts 使用独立 chunk。固定 fixture 使用 1,500/5,000 个权益点、200 个事件以及 500 根 K 线/100 个 marker；在同一生产 Chromium 连续测量 5 次，要求数据就绪到 chart-ready 中位数不超过 100 ms、平移缩放平均不低于 55 FPS，且图表主线程任务不超过 50 ms。

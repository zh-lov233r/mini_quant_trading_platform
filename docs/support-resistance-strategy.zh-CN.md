# 支撑线与压力线策略

[English](support-resistance-strategy.md) | [文档索引](README.zh-CN.md)

`support_resistance` 是 engine-ready、long-only 的日线策略。新执行统一使用“已确认 Pivot + ATR 倾斜区 + 四状态分区”因果检测器 `pivot-slope-regime-v3`。旧 `pivot-slope-atr-v2` 只保留用于读取历史策略、回测和物化证据，不能用于创建新执行。注册或创建策略不会创建 allocation、激活 portfolio、开启 scheduler 或提交订单。

共享 C++ 内核是唯一可执行实现。descriptor 统一拥有默认值、校验、特征/历史声明和算法 revision；原生状态对象统一管理 Pivot 成员、区域、状态、pending outcome、posterior 证据、入场通道、信号、退出和审计事件。Python 只加载并指纹化数据、查询/锁定缓存表、把 typed 结果适配为持久化行，以及执行另行授权的 Paper 券商操作。回测调用原生 `run_backtest`；Paper 把有界历史转换为内存 PreparedDataset v3，并调用 `evaluate_day(dataset_day, strategy, portfolio_state)`。

## 时序与价格语义

T 日只能使用 T-1 日收盘后冻结的区域。T 日判断结束后才追加当前 K 线；Pivot 必须等配置的右侧 K 线完整后才能确认，更新后的区域最早在 T+1 可见。因此当前或未来 K 线不能反向确认历史信号。

信号价格优先使用前复权 OHLC，缺失时回退到未复权 OHLC；成交量、ATR14 和 ADV20 来自同一日快照。T 日收盘生成信号，在下一有效交易日开盘成交。原有拆股处理、日期/标的确定性顺序和 long-only 语义不变。生命周期蜡烛图请求相同的复权价格。

## 默认检测参数

- Pivot 左右各 3 根 K 线。
- 检测窗口 120 个交易日。
- 高点与低点 Pivot 独立进行确定性的两阶段、时间衰减加权 Theil-Sen 拟合。
- 每条线至少需要 3 个内点，参与拟合的 Pivot 对至少跨 10 个交易日。
- 第一阶段内点容差为 0.75 ATR，最大绝对斜率为每交易日 0.25 ATR。
- 区域版本创建时冻结 0.5 ATR 半宽；衰减半衰期为 60 个交易日。
- 低点 Pivot 与高点 Pivot 各保留质量最高的一条边界，因此任一交易日每个标的最多一条下边界和一条上边界；边界显示角色翻转时不会丢失另一侧。
- 质量依次按内点数、时间衰减权重、ATR 标准化残差、距现价距离和稳定 key 排序；无法拟合时不回退为水平区域。
- 连续停留在区域内只计一次触碰。
- 放量确认突破先使用 T-1 冻结的压力区几何完成 T 日决策。突破事件只用于审计，不直接形成可执行买入；收盘站上压力区上沿后显示角色转为支撑。新拟合线若已经位于当前收盘下方，也会初始化为支撑。后续回踩只有在上方重新存在有效压力区时，才能作为独立 `breakout_retest` 候选。支撑收盘跌破事件日下沿后转为潜在压力。Pivot 成员少于阈值或离开窗口时区域失效。
- 投影后出现非有限值、非正价格或上下界错序的区域，会在参与分类和候选检测前失效；不可变 tombstone 冻结最后一个有效几何，并且不会越过失效交易日继续投影。持久化层会拒绝超过数据库数值域的几何，不扩大精度，也不静默裁剪价格。

## 四状态时间分区

每个可见行情交易日必须且只能属于 `uptrend`（上行）、`downtrend`（下行）、`range`（震荡）或 `transition`（过渡）之一。T 日分类只使用 T-1 已冻结边界、T 日收盘和截至 T 已确认的 Pivot；T 日新建区域最早在 T+1 参与分类。

- 上行：高低点结构和上下边界方向全部向上，且下边界未被破坏。
- 下行：高低点结构和上下边界方向全部向下，且上边界未被破坏。
- 震荡：价格位于有序上下边界内，结构为横向、收敛或扩张，Pivot 与边界方向一致。
- 过渡：缺少任一边界或 Pivot 证据、上下边界错位、价格离开结构、方向冲突或边界破坏。

方向容差由已有 ATR 区域半宽和最小拟合跨度派生，没有新增 regime 参数。首个行情日即使证据不足也写入 `transition`，状态只在分类结果改变时追加新版本，因此重建后的时间区间连续、互斥且无缺口。这里的不重叠只约束时间状态；支撑与压力价格带仍可相交。

## 入场、评分与退出

三种检测模式默认全部开启，且参数校验要求至少开启一种：

- `support_bounce`：前收盘位于支撑区上方，新 K 线进入区域，随后收盘重新站上上沿加 0.25 ATR。
- `resistance_breakout`：收盘超过压力区上沿加 0.5 ATR，且成交量至少为 ADV20 的 1.5 倍；只保存突破候选和审计证据，不交易。
- `breakout_retest`：突破后 10 个交易日内回踩事件当日投影后的原压力区，收盘守住上沿，回踩量不超过突破量的 0.8。

同日命中的全部模式都会保存为候选事件。上行只允许 `support_bounce` 与 `breakout_retest`，震荡只允许 `support_bounce`，下行和过渡禁止买入；`resistance_breakout` 固定拒绝为 `direct_breakout_audit_only`。被拒绝的候选仍保存原因与分类证据。严格向前展开的 Beta posterior 只保留为形态统计证据，不参与候选排序。

买入还必须位于由角色区域组成的有效内沿通道：选择收盘价下方最近的活动支撑区和上方最近的活动压力区，要求 `支撑区上沿 < 压力区下沿`，并使用闭区间 `支撑区上沿 ≤ 价格 ≤ 压力区下沿`。信号日冻结两区快照、key、内沿、斜率和原因；缺少任一区、内沿交叉或收盘越界都会拒绝候选。回测按一个交易日投影通道，并以含滑点的下一有效交易日（T+1）开盘模拟成交价再次校验；越界只写 `execution_rejection`，不改变现金、持仓或成交。SELL、止损和清仓不受通道限制。

只有 T 日以前已经结束的同类事件进入 Beta 统计；20 日内先到 3 ATR 目标记成功，先到 1.5 ATR 止损记失败，均未触及记 censored，同日同时触及两边保守记失败。

入场信号把选中区域、状态与分类证据、斜率、锚点交易序号、Pivot 证据、全部候选模式、事件日上下界、ATR、目标、止损、后验样本量与 signal strength 冻结在 `Signal.features.support_resistance`。多头止损取冻结区域下沿、1.5 ATR 与 8% 最大亏损线中最严格者。目标优先使用上方最近压力区；预期盈亏比低于 1.5 时跳过入场。没有有效压力区时使用 3 ATR 目标。最长持有 40 个交易日。持仓退出优先级固定为止损、止盈、确认下行、最长持仓；过渡状态不强制退出。退出仍在 T 收盘生成信号、下一有效交易日开盘成交。

## 稀疏持久化与缓存失效

系统不会保存全市场逐日完整区域快照。只有 Pivot 成员、角色或状态变化时才新增区域版本，同时保存本次回测或 paper signal 实际看到的事件：

- `support_resistance_materializations`：不可变缓存身份与构建状态。
- `support_resistance_zone_versions`：稀疏区域版本时间线。
- `support_resistance_regime_versions`：每次状态开始的 append-only 版本，包含上下边界 key、方向、原因码和完整分类证据。
- `support_resistance_run_materializations`：运行与共享缓存的审计关联。
- `support_resistance_run_events`：触碰、突破、回踩、候选、选择、通道起止、信号/成交拒绝、评分结果、角色转换和失效事件。

缓存身份包含 v3 算法、检测器 revision、regime-logic revision、规范化检测参数、价格语义、标的集合哈希、覆盖区间和源数据指纹，不能复用 v2 materialization。只有覆盖起止与请求完全相同的物化可复用，避免交易日序号偏移改变投影价格。系统在读取行情前冻结指纹并在持久化前再次校验；若运行中数据变化或状态完整性检查失败，整个构建失败，不能保存为 completed 缓存。区域和状态版本均不可回写；未来数据只能追加版本或生成新 materialization。

首个明细写入前，持久化层会完整校验 typed 列长/顺序、枚举与 JSON、稳定 instrument 引用、有限数/数据库数值边界、完整状态时间线和投影后的 zone/event 几何。PostgreSQL 使用当前事务的 psycopg3 `COPY FROM STDIN` connection，以每批 5,000 行写入 zone version、regime version 和 run event，并在每批前检查取消。批次不独立提交；校验、COPY、取消或物化任一失败都会回滚整次运行结果。

检测器状态和稀疏时间线按稳定的 `instrument_id` 分区，`symbol` 只作为展示元数据。原生回测结果会把该身份直接传入持久化；非原生调用方只可在请求覆盖区间内使用唯一的主代码历史映射。同一 ticker 在区间内属于多个 instrument 时，各自历史保持独立，不再合并，也不会用当前 canonical ticker 猜测。现有数据库需要在另行授权的 schema 上线中重新运行 `backend/utils/migrate_pivot_slope_regime_v3.sql`，把 regime 唯一性切换到 `instrument_id`；执行前仍须完成文档要求的只读预检和备份。

paper trading 会先完成缓存物化和运行事件持久化。支撑/压力 BUY 在夜间仅写入 `paper_execution=pending`，下一券商交易日开盘后以当前时段最新卖价校验投影通道；通过后提交以压力内沿为上限的普通时段 day 限价单，并在报价离开通道或纽约时间 09:35 时取消余量。限价单无法保证最低成交价；低于支撑内沿的成交会记录 `channel_fill_violation`、取消余量，并在持仓归零前禁止加仓，但不会自动卖出。SELL 仍优先且不受通道限制。构建失败会把策略运行标记为 failed，并且不会提交订单。

相关配置为 `ALPACA_DATA_BASE_URL`（默认 `https://data.alpaca.markets`）、`ALPACA_DATA_FEED`（`iex` 或 `sip`）、`PAPER_TRADING_OPEN_QUOTE_MAX_AGE_SECONDS`（默认 15 秒）和 `PAPER_TRADING_OPEN_ENTRY_CUTOFF_NY`（默认 `09:35`）。`submit_orders=false` 只记录 dry-run，不进入开盘执行队列。

删除运行会级联删除该运行的关联和事件，但保留共享 materialization 与区域版本。系统不会自动删除未引用缓存；第一版按需生成，不会自动预热十年全市场历史。

在另行授权预热或 schema 上线前，可以用只读 dry-run 估算源行数和稀疏版本数：

```bash
.venv/bin/python backend/utils/plan_support_resistance_materialization.py \
  --symbols AAPL,MSFT --start-date 2024-01-01 --end-date 2025-12-31
```

请求区间需要自行包含希望纳入的预热历史。该命令在只读事务中执行，并报告已加载标的、关联源行数以及检测器推导的区域版本/事件估算，不写入数据库。

`backend/utils/plan_support_resistance_cache_cleanup.py` 是只读工具，只报告未引用缓存的精确 ID，并刻意不提供 apply 模式；实际删除需要另行明确授权。

只读审计接口为：

```text
GET /api/backtests/{run_id}/support-resistance?symbol=AAPL&start_date=2025-01-01&end_date=2025-12-31
```

每个区域版本返回按查询窗口裁剪的 `geometry`。`regime_intervals` 则返回与查询窗口相交的完整原始闭区间，包括起止日、交易日数、上下边界 key、原因和证据；旧 v2 回测返回空数组。区间交易日历由同时具备日线与日频特征的交易日重建，并从该物化 symbol 身份的首个已持久化状态开始。symbol 必须精确匹配，zone key 命中任一上下边界。

生命周期图最底层绘制互斥状态背景：上行绿、下行红、震荡琥珀、过渡灰；其上依次为支撑/压力价格带、青色有效交易通道、K 线和成交量、信号与成交标记。旧结果没有通道事件时明确显示“旧结果未计算有效交易通道”，不会用新规则反推。每个状态起点有分界线，宽区间显示“状态 + 交易日数”，hover/click 显示完整证据。前端会再次按物化覆盖范围内的可见行情日验证唯一覆盖；发现重叠、缺口或重复日期时停止绘制状态背景并显示完整性错误，绝不静默叠画。超过物化结束日的卖出后 K 线继续显示，并有意保持无状态背景。

## 数据库上线与恢复

本仓库没有 Alembic 迁移流程，应用启动也不会自动创建这些表。

1. 明确记录目标数据库并创建可恢复备份。
2. 停止或排空支撑压力回测和 paper signal 运行，同时保持 scheduler / order submission 关闭。
3. 新数据库审查并应用 `backend/utils/create_zzzzzz_support_resistance.sql`；已有 v2 数据库在同一事务内应用仅新增表和索引的 `backend/utils/migrate_pivot_slope_regime_v3.sql`。两者都必须使用 `ON_ERROR_STOP`。
4. 运行只读检查：

   ```bash
   .venv/bin/python backend/utils/check_support_resistance_integrity.py --json
   ```

5. 部署后端和前端代码；schema 上线不得顺带激活 allocation。

回滚时先回滚应用；新增状态表可留作审计。若明确授权删除，先备份并确认没有 v3 materialization 引用，再删除状态表。不能为了修复陈旧缓存直接 drop 表。EOD、复权价格或 `daily_features` 修正后重新运行目标回测即可；新源指纹会生成新 materialization，被运行引用的旧证据继续保留。

## 验证

先运行聚焦测试，再运行受影响的完整检查：

```bash
.venv/bin/python -m unittest backend.tests.test_support_resistance_strategy -v
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
.venv/bin/python -m compileall -q backend/src backend/utils backend/tests
cd frontend && npm run lint && npm run build
```

这些结果只构成研究证据，不代表盈利能力或实盘安全性。

旧算法的有效性结论不能继承到 v3。独立的 `pivot-slope-regime-v3` 有效性协议、历史动态流动性股票池、封存留出和报告产物见[支撑/压力区策略有效性研究](support-resistance-effectiveness.zh-CN.md)。

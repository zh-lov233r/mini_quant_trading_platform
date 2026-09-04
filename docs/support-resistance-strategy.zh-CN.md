# 支撑线与压力线策略

[English](support-resistance-strategy.md) | [文档索引](README.zh-CN.md)

`support_resistance` 是 engine-ready、long-only 的日线策略。新执行统一使用“已确认 Pivot + ATR 倾斜区 + 四状态分区”因果检测器 `pivot-slope-regime-v3`。旧 `pivot-slope-atr-v2` 只保留用于读取历史策略、回测和物化证据，不能用于创建新执行。注册或创建策略不会创建 allocation、激活 portfolio、开启 scheduler 或提交订单。

共享 C++ 内核是唯一可执行实现。descriptor 统一拥有默认值、校验、特征/历史声明和算法 revision；原生状态对象统一管理 Pivot 成员、区域、状态、pending outcome、posterior 证据、入场通道、信号、退出和审计事件。Python 只加载并指纹化数据、查询/锁定缓存表、把 typed 结果适配为持久化行，以及执行另行授权的 Paper 券商操作。回测调用原生 `run_backtest`；Paper 把有界历史转换为内存 PreparedDataset v4，并调用 `evaluate_day(dataset_day, strategy, portfolio_state)`。

## 时序与价格语义

T 日只能使用 T-1 日收盘后冻结的区域。T 日判断结束后才追加当前 K 线；Pivot 必须等配置的右侧 K 线完整后才能确认，更新后的区域最早在 T+1 可见。因此当前或未来 K 线不能反向确认历史信号。

信号价格优先使用前复权 OHLC，缺失时回退到未复权 OHLC；成交量、ATR14 和 ADV20 来自同一日快照。T 日收盘生成信号，在下一有效交易日开盘成交。原有拆股处理、日期/标的确定性顺序和 long-only 语义不变。生命周期蜡烛图请求相同的复权价格。

## 默认检测参数

- Pivot 左右各 3 根 K 线，窗口 120 个交易日。与极值之差不超过候选 ATR 的 `pivot_tolerance_atr=0.05` 时视为并列，在确认窗口内取最早一根；不会读取右侧确认窗口之外的未来 K 线。
- 从符合跨度的高/高、低/低 Pivot 对生成候选，复用确定性两阶段加权 Theil-Sen 拟合。每条线至少 3 个内点、配对跨度至少 10 日；去重成员及同类重叠拟合后，每类保留 `max_zones_per_kind=3` 条（可设 1–5）。
- 按内点数、平均新近权重、ATR 标准化残差、距现价距离、稳定 Pivot key 排序；衰减半衰期 60 日，不增加水平线回退。
- 半宽取 `zone_half_width_atr × 当日 ATR`（默认 0.5）与成员最大绝对拟合残差中的较大值，保证定义区域的 Pivot 在其发生日位于带内。成员不变时保持锚点；当日 ATR 超出锚点 ATR 的 `[0.5, 2]` 倍范围时重建锚点并追加稀疏版本，不改写历史。
- 三种 ATR 分别有明确用途：各 Pivot ATR 用于内点容差（默认 0.75），成员加权中位数 ATR 用于斜率上限（默认每日 0.25 ATR），当日 ATR 用于半宽下限。残差半宽以价格计量。
- `pivot_count` 只统计拟合成员。`touch_count` 从零开始，只在实际观察到由不相交变为与冻结区域相交时递增；连续停留只计一次。触碰表示相交，不代表支撑必然守住。
- 每次决策后按收盘相对投影中心的位置重算显示角色；形态、通道和目标不依赖该标签。关闭直接突破审计开关仍保留放量突破记录，因此不会关闭回踩识别。
- 非有限、非正或错序投影在分类前失效，保留最后有效 tombstone 几何和数据库数值校验。

## 四状态时间分区

每个可见交易日唯一属于上行、下行、震荡或过渡。T 日使用 T-1 冻结边界和已经确认的 Pivot，按质量选择高低边界。结构方向独立取最近四个已确认高/低摆动点的两两 ATR 标准化价差中位数，不再只取拟合内点；证据记录全部参与 key。

上行要求上下边界及高低点结构均向上，且下边界完整；下行要求四项均向下，且上边界完整。有序、包含价格的横向/收敛/扩张结构为震荡，缺失、破坏或方向冲突为过渡。继续保留四项方向一致门槛，不因过渡占比偏高而直接放宽交易规则。状态时间线仍追加写入、连续且互斥。每天重新分类，但信号中的 `regime_evidence` 统一引用该状态区间起点证据，并以 `evidence_trade_date` 标明日期，使新计算和缓存重放完全一致；入场通道另存当前日几何。

## 入场、评分与退出

三个检测开关默认开启，但必须至少启用**反弹或回踩**：

- `support_bounce`：前收盘在带上方，新 K 线与区域相交，收盘恢复到上沿加 0.25 ATR。
- `resistance_breakout`：跨过冻结上沿加 0.5 ATR，成交量至少为 ADV20 的 1.5 倍。开关只控制审计候选，不直接买入。
- `breakout_retest`：确认突破后的 10 个交易日内回踩投影区域，收盘守住上沿，成交量不超过突破日的 0.8 倍。

上行允许反弹和回踩，震荡只允许反弹，下行/过渡禁止买入。保留全部候选及拒绝原因，确定性选择合格候选中最强者。强度模型 v2 权重为：盈亏比 25%、Pivot 数量 15%、真实触碰 10%、拟合质量 15%、距支撑近度 20%、成交量 15%。通过确认后，离支撑更近得分更高；直接突破以确认幅度替代近度，回踩奖励缩量。最低强度仍为 50；这些固定权重是工程默认值，不是盈利有效性证据。

通道按几何选边：不看角色或 Pivot 类型，选收盘下方最近的活动区域上沿、上方最近的活动区域下沿，要求前者严格小于后者。冻结两区后向前投影一个交易日，在下一有效交易日含滑点开盘价处复核闭区间。不会自动放宽上沿；拒单记录 `execution_rejection`，现金与持仓不变，SELL 不受通道门槛限制。

初始止损取区域下沿、1.5 ATR、8% 收盘止损参考线中最严格者（配置名保留 `max_loss_pct`）。目标取上方最近区域，不依赖角色；不存在时回退 3 ATR。候选新增 `overhead_count` 与 `target_source`。信号日毛盈亏比至少 1.5，T+1 入场时按实际模拟成交价，**扣除双边手续费和退出滑点后**再次检查。信号强度衡量毛形态，执行门槛使用实际模拟成本。

`risk_per_trade_pct=0.005` 把每笔计划止损风险预算设为权益的 0.5%，包含模拟双边手续费。数量同时受该风险预算、可用现金及作为名义仓位上限的 `position_size_pct` 约束。卖出优先、共享现金和确定性买入排序保持串行。跳空损失仍可超过预算，8% 参考线和按风险定量都不是最大损失保证。

持仓止损沿冻结支撑下沿按后续已观测交易日向前投影，且不低于初始参考线；**此前收盘**达到 `break_even_at_r=1` 倍初始风险盈利后，止损提高到买入成本。止损信号阻止同一区域在随后 `stop_cooldown_sessions=5` 个交易日再入场（0 关闭额外冷却）。Paper 只加载同策略/组合、截至请求日期的历史止损信号。退出优先级仍是收盘止损、目标、确认下行、最长持有 40 日；均在下一有效交易日开盘成交。

Beta 证据只作描述统计，不参与排序或下单。每个 instrument/模式同时最多追踪一个合格事件，按下一开盘价复核通道/盈亏比，使用相同的收盘止损、目标、下行及持有期限规则，并在随后开盘标注退出。超时计入非成功：`(wins + 1) / (wins + losses + censored + 2)`；当日结局最早次日可用，未入场不计样本。这是毛收益假设形态统计，排除组合现金和持仓名额竞争和实际券商费用，**不是**实际组合绩效或校准置信区间。删除独立的 `score_outcome_window`、`score_target_atr`、`score_stop_atr` 配置。

## 可选大盘过滤

默认关闭。`market_filter_enabled=true` 时，使用 `market_filter_symbol`（默认 `SPY`，可指定已存储的 A 股指数）。信号日基准前复权收盘不得低于最近 200 个已观测收盘的均值；缺少当日行情或历史不足会拒绝 BUY，不使用未来行情，也不向前填充基准。已有持仓继续原退出规则。

策略不读取行业分类，也不按行业限制持仓；缺少 SIC 不会阻止新增仓位。证券主表的 SIC 资料和股票池行业筛选保持独立。最大持仓数、单票名义仓位上限和单笔风险预算继续生效，但不控制行业集中度。

回测和 Paper 信号运行把大盘输入冻结在 `config_snapshot.support_risk_context`，门槛与定量由共享 C++ 实现。Paper 按零模拟手续费，以通道与最低盈亏比共同限制的限价计算数量，避免用较低当前报价授权更高的最坏计划损失；不预测实际券商费用，也不保证跳空保护。

## 只读审查与版本更新

检测器 revision **12**、状态 revision **3**、强度 v2 会改变缓存身份，不修改数据库表，本次不需要迁移或重置。从当前 catalog 重建策略，或从参数中删除已移除的评分字段和 `risk.max_industry_positions`；按两种可交易模式创建新的有效性研究。旧结果保留为审计记录，不构成新规则证据；不要用修订协议继续旧三模式研究。

Revision 12 在选择或记录新建/重拟合区域前，校验按 `NUMERIC(24,10)` 舍入后的最终几何。原始正下界或 ATR 舍入为零时，拒绝该候选；投影和持久化校验保持不变，不钳制价格、不扩大数据库精度。这可防止收尾阶段才出现 `zone geometry is non-positive or unordered` 物化失败。先执行 `.venv/bin/pip install --no-build-isolation --no-deps -e backend/native` 重建原生扩展，等活动任务结束或经明确授权取消后，再重启后端和 worker；已加载的原生模块不会热更新。仅验证回测时保持 `PAPER_TRADING_SCHEDULER_ENABLED=false`、`PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false`。完成这些检查后，仅重试已定位的失败回测并保留原失败记录；不需要数据库修复或行情修改。

```bash
.venv/bin/python backend/utils/audit_support_resistance_run.py --run-id <run-uuid>
```

命令使用可重复读、只读事务，报告候选/状态/通道/强度漏斗、盈亏比和强度离散度、状态交易日覆盖、拒绝原因及后续 1/5/10/20 日收益、止损后下一开盘跳空。后续收益和交易日覆盖使用当前存储行情，可能包含后来修正；旧事件没有 `overhead_count` 时不能确定 ATR 目标回退率。少量或重叠拒单样本不能证明应放宽开盘通道。物化规划器报告实际区域/状态/事件数和每批 5,000 行的 COPY 批次数估算。

## 稀疏持久化与缓存失效

系统不会保存全市场逐日完整区域快照。只有 Pivot 成员、角色、状态或波动重锚变化时才新增区域版本，同时保存本次回测或 paper signal 实际看到的事件：

- `support_resistance_materializations`：不可变缓存身份与构建状态。
- `support_resistance_zone_versions`：稀疏区域版本时间线。
- `support_resistance_regime_versions`：每次状态开始的 append-only 版本，包含上下边界 key、方向、原因码和完整分类证据。
- `support_resistance_run_materializations`：运行与共享缓存的审计关联。
- `support_resistance_run_events`：触碰、突破、回踩、候选、选择、通道起止、信号/成交拒绝、评分结果、角色转换和失效事件。

结构缓存身份包含 v3 算法、检测器 revision、regime-logic revision、规范化检测参数、价格语义、标的集合哈希和覆盖区间，不能复用 v2 materialization。只有覆盖起止与请求完全相同且 `invalidated_at IS NULL` 的物化可复用，避免交易日序号偏移改变投影价格。任何受支持的行情写入前，排他维护窗口会先失效当前记录；后续运行可用同一结构键创建新的当前物化，同时保留历史运行链接。状态完整性检查失败时整个构建仍然失败。区域和状态版本均不可回写。

首个明细写入前，持久化层会完整校验 typed 列长/顺序、枚举与 JSON、稳定 instrument 引用、有限数/数据库数值边界、完整状态时间线和投影后的 zone/event 几何。PostgreSQL 使用当前事务的 psycopg3 `COPY FROM STDIN` connection，以每批 5,000 行写入 zone version、regime version 和 run event，并在每批前检查取消。批次不独立提交；校验、COPY、取消或物化任一失败都会回滚整次运行结果。

检测器状态和稀疏时间线按稳定的 `instrument_id` 分区，`symbol` 只作为展示元数据。原生回测结果会把该身份直接传入持久化；非原生调用方只可在请求覆盖区间内使用唯一的主代码历史映射。同一 ticker 在区间内属于多个 instrument 时，各自历史保持独立，不再合并，也不会用当前 canonical ticker 猜测。现有数据库需要在另行授权的 schema 上线中重新运行 `backend/utils/migrate_pivot_slope_regime_v3.sql`，把 regime 唯一性切换到 `instrument_id`；执行前仍须完成文档要求的只读预检和备份。

paper trading 会先完成缓存物化和运行事件持久化。支撑/压力 BUY 在夜间仅写入 `paper_execution=pending`，下一券商交易日开盘后以当前时段最新卖价校验投影通道；通过后提交以压力内沿和盈亏比价格上限中的较小者为上限的普通时段 day 限价单，并在报价离开通道或纽约时间 09:35 时取消余量。限价单无法保证最低成交价；低于支撑内沿的成交会记录 `channel_fill_violation`、取消余量，并在持仓归零前禁止加仓，但不会自动卖出。SELL 仍优先且不受通道限制。构建失败会把策略运行标记为 failed，并且不会提交订单。

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

回滚时先回滚应用；新增状态表可留作审计。若明确授权删除，先备份并确认没有 v3 materialization 引用，再删除状态表。不能为了修复陈旧缓存直接 drop 表。通过维护流水线修正 EOD、复权价格或 `daily_features` 后重新运行目标回测；已失效的历史物化继续作为运行证据保留，并生成新的当前物化。

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

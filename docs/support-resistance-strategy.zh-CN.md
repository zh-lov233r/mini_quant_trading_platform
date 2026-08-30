# 支撑线与压力线策略

[English](support-resistance-strategy.md) | [文档索引](README.zh-CN.md)

`support_resistance` 是 engine-ready、long-only 的日线策略。回测、研究 trial 和 paper signal 共用同一个“已确认 Pivot + ATR 倾斜区”因果检测器 `pivot-slope-atr-v2`。注册或创建策略不会创建 allocation、激活 portfolio、开启 scheduler 或提交订单。

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
- 每个角色只保留质量最高的一条线，因此任一交易日每个标的最多一条支撑和一条压力。
- 质量依次按内点数、时间衰减权重、ATR 标准化残差、距现价距离和稳定 key 排序；无法拟合时不回退为水平区域。
- 连续停留在区域内只计一次触碰。
- 放量确认突破先使用 T-1 冻结的压力区几何完成 T 日决策。T 日决策冻结后，只要收盘站上压力区上沿，显示角色就转为支撑；成交量确认仍只决定是否形成可交易的 `resistance_breakout` 模式。新拟合线若已经位于当前收盘下方，也会初始化为支撑，而不会误标成上方压力。后续成功回踩只确认独立的 `breakout_retest` 模式，不再延迟角色转换。支撑收盘跌破事件日下沿后转为潜在压力。Pivot 成员少于阈值或离开窗口时区域失效。

## 入场、评分与退出

三种模式默认全部开启，且参数校验要求至少开启一种：

- `support_bounce`：前收盘位于支撑区上方，新 K 线进入区域，随后收盘重新站上上沿加 0.25 ATR。
- `resistance_breakout`：收盘超过压力区上沿加 0.5 ATR，且成交量至少为 ADV20 的 1.5 倍。
- `breakout_retest`：突破后 10 个交易日内回踩事件当日投影后的原压力区，收盘守住上沿，回踩量不超过突破量的 0.8。

同日命中的全部模式都会保存为运行事件，但只生成一个 BUY：先按严格向前展开的 Beta 后验评分排序，再按固定顺序 `breakout_retest`、`support_bounce`、`resistance_breakout` 打破平分。无历史样本为 0.5。只有 T 日以前已经结束的同类事件进入后验；20 日内先到 3 ATR 目标记成功，先到 1.5 ATR 止损记失败，均未触及记 censored，同日同时触及两边保守记失败。

入场信号把选中区域、斜率、锚点交易序号、Pivot 证据、全部候选模式、事件日上下界、ATR、目标、止损、后验样本量与评分冻结在 `Signal.features.support_resistance`。多头止损取冻结区域下沿、1.5 ATR 与 8% 最大亏损线中最严格者。目标优先使用上方最近压力区；预期盈亏比低于 1.5 时跳过入场。没有有效压力区时使用 3 ATR 目标。最长持有 40 个交易日。

## 稀疏持久化与缓存失效

系统不会保存全市场逐日完整区域快照。只有 Pivot 成员、角色或状态变化时才新增区域版本，同时保存本次回测或 paper signal 实际看到的事件：

- `support_resistance_materializations`：不可变缓存身份与构建状态。
- `support_resistance_zone_versions`：稀疏区域版本时间线。
- `support_resistance_run_materializations`：运行与共享缓存的审计关联。
- `support_resistance_run_events`：触碰、突破、回踩、候选、选择、评分结果、角色转换和失效事件。

缓存身份包含算法版本、规范化检测参数、价格语义、标的集合哈希、覆盖区间和源数据指纹。v2 第一版只复用覆盖起止与请求完全相同的 materialization，避免交易日序号偏移改变投影价格。检测器身份同时包含 Pivot/ATR 设置、会改变区域角色的突破/回踩阈值，以及内部实现修订号，因此因果或序列化修复不会静默复用旧检测器生成的结果。当前指纹刻意使用全局 instrument / symbol-history 版本，以及 EOD / daily feature 行数和最新修订时间；这可能使更多缓存失效，但不会在标的身份、复权价格或特征修正后静默复用旧输出。系统在读取行情前冻结指纹，并在持久化前再次校验；若运行中数据发生变化，则该运行失败，不能把旧计算结果标成新数据版本。只有覆盖起止完全相同的 completed 物化可以关联到新运行；failed 或 building 状态不能作为 completed 缓存使用。

paper trading 会先完成缓存物化和运行事件持久化，再进入订单执行循环。构建失败会把策略运行标记为 failed，并且不会提交新的 paper order。注册 handler 仍不会创建 allocation、激活策略/portfolio 或开启 scheduler。

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

每个区域版本返回按查询窗口裁剪的 `geometry`：起止交易日、两端中心/上下界和 `slope_per_session`。生命周期图会再次排除与可见 K 线无交集的版本，以 Canvas 四边形绘制倾斜区域；仅 BUY/SELL 信号和成交保留文字，同日同类次要审计事件会聚合，并支持 hover 与点击固定详情。区域自动缩放只纳入与可见 K 线价格范围或其 25% 跨度缓冲相交的覆盖层，因此远端历史区域仍可审计，但不会压扁当前 K 线。

## 数据库上线与恢复

本仓库没有 Alembic 迁移流程，应用启动也不会自动创建这些表。

1. 明确记录目标数据库并创建可恢复备份。
2. 停止或排空支撑压力回测和 paper signal 运行，同时保持 scheduler / order submission 关闭。
3. 新数据库审查并应用 `backend/utils/create_zzzzzz_support_resistance.sql`；已有 v1 数据库在同一事务内先删除已授权的 v1 证据，再应用 `backend/utils/migrate_pivot_slope_atr_v2.sql`。两者都必须使用 `ON_ERROR_STOP`。
4. 运行只读检查：

   ```bash
   .venv/bin/python backend/utils/check_support_resistance_integrity.py --json
   ```

5. 部署后端和前端代码；schema 上线不得顺带激活 allocation。

回滚时先回滚应用。新增表可以保留用于审计和向后兼容。如果用户明确授权删除，先备份四张表，再先删运行事件/关联表，最后删共享区域/清单表。不能为了修复陈旧缓存直接 drop 表。EOD、复权价格或 `daily_features` 修正后重新运行目标回测即可；新源指纹会生成新 materialization，被运行引用的旧证据继续保留。

## 验证

先运行聚焦测试，再运行受影响的完整检查：

```bash
.venv/bin/python -m unittest backend.tests.test_support_resistance_strategy -v
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
.venv/bin/python -m compileall -q backend/src backend/utils backend/tests
cd frontend && npm run lint && npm run build
```

这些结果只构成研究证据，不代表盈利能力或实盘安全性。

`pivot-atr-v1` 的有效性结论不能继承到倾斜算法。独立的 `pivot-slope-atr-v2` 有效性协议、历史动态流动性股票池、封存留出和报告产物见[支撑/压力区策略有效性研究](support-resistance-effectiveness.zh-CN.md)。

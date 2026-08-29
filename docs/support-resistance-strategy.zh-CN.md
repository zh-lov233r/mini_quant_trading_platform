# 支撑线与压力线策略

[English](support-resistance-strategy.md) | [文档索引](README.zh-CN.md)

`support_resistance` 是 engine-ready、long-only 的日线策略。回测、研究 trial 和 paper signal 共用同一个“已确认 Pivot + ATR 聚类”因果检测器。注册或创建策略不会创建 allocation、激活 portfolio、开启 scheduler 或提交订单。

## 时序与价格语义

T 日只能使用 T-1 日收盘后冻结的区域。T 日判断结束后才追加当前 K 线；Pivot 必须等配置的右侧 K 线完整后才能确认，更新后的区域最早在 T+1 可见。因此当前或未来 K 线不能反向确认历史信号。

信号价格优先使用前复权 OHLC，缺失时回退到未复权 OHLC；成交量、ATR14 和 ADV20 来自同一日快照。T 日收盘生成信号，在下一有效交易日开盘成交。原有拆股处理、日期/标的确定性顺序和 long-only 语义不变。生命周期蜡烛图请求相同的复权价格。

## 默认检测参数

- Pivot 左右各 3 根 K 线。
- 检测窗口 120 个交易日。
- 聚类半径 0.75 ATR，区域半宽 0.5 ATR。
- 至少 2 个有效 Pivot，衰减半衰期 60 个交易日。
- 每个标的最多保留 5 个支撑区和 5 个压力区。
- 中心价采用带时间衰减的 Pivot 价格加权中位数。
- 连续停留在区域内只计一次触碰。
- 压力突破并成功回踩后转为支撑；支撑收盘跌破下沿后转为潜在压力。Pivot 成员少于阈值或离开窗口时区域失效。

## 入场、评分与退出

三种模式默认全部开启，且参数校验要求至少开启一种：

- `support_bounce`：前收盘位于支撑区上方，新 K 线进入区域，随后收盘重新站上上沿加 0.25 ATR。
- `resistance_breakout`：收盘超过压力区上沿加 0.5 ATR，且成交量至少为 ADV20 的 1.5 倍。
- `breakout_retest`：突破后 10 个交易日内回踩原压力区，收盘守住上沿，回踩量不超过突破量的 0.8。

同日命中的全部模式都会保存为运行事件，但只生成一个 BUY：先按严格向前展开的 Beta 后验评分排序，再按固定顺序 `breakout_retest`、`support_bounce`、`resistance_breakout` 打破平分。无历史样本为 0.5。只有 T 日以前已经结束的同类事件进入后验；20 日内先到 3 ATR 目标记成功，先到 1.5 ATR 止损记失败，均未触及记 censored，同日同时触及两边保守记失败。

入场信号把选中区域、全部候选模式、上下界、ATR、目标、止损、后验样本量与评分冻结在 `Signal.features.support_resistance`。多头止损取冻结区域下沿、1.5 ATR 与 8% 最大亏损线中最严格者。目标优先使用上方最近压力区；预期盈亏比低于 1.5 时跳过入场。没有有效压力区时使用 3 ATR 目标。最长持有 40 个交易日。

## 稀疏持久化与缓存失效

系统不会保存全市场逐日完整区域快照。只有 Pivot 成员、角色或状态变化时才新增区域版本，同时保存本次回测或 paper signal 实际看到的事件：

- `support_resistance_materializations`：不可变缓存身份与构建状态。
- `support_resistance_zone_versions`：稀疏区域版本时间线。
- `support_resistance_run_materializations`：运行与共享缓存的审计关联。
- `support_resistance_run_events`：触碰、突破、回踩、候选、选择、评分结果、角色转换和失效事件。

缓存身份包含算法版本、规范化检测参数、价格语义、标的集合哈希、覆盖区间和源数据指纹。检测器身份同时包含 Pivot/ATR 设置、会改变区域角色的突破/回踩阈值，以及内部实现修订号，因此因果或序列化修复不会静默复用旧检测器生成的结果。当前指纹刻意使用全局 instrument / symbol-history 版本，以及 EOD / daily feature 行数和最新修订时间；这可能使更多缓存失效，但不会在标的身份、复权价格或特征修正后静默复用旧输出。系统在读取行情前冻结指纹，并在持久化前再次校验；若运行中数据发生变化，则该运行失败，不能把旧计算结果标成新数据版本。覆盖请求区间的 completed 物化可以关联到新运行；failed 或 building 状态不能作为 completed 缓存使用。

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
GET /api/backtests/{run_id}/support-resistance?symbol=AAPL&zone_key=...&start_date=2025-01-01&end_date=2025-12-31
```

## 数据库上线与恢复

本仓库没有 Alembic 迁移流程，应用启动也不会自动创建这些表。

1. 明确记录目标数据库并创建可恢复备份。
2. 停止或排空支撑压力回测和 paper signal 运行，同时保持 scheduler / order submission 关闭。
3. 审查后显式使用 `ON_ERROR_STOP` 应用 `backend/utils/create_zzzzzz_support_resistance.sql`。
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

固定 `pivot-atr-v1` 有效性协议、历史动态流动性股票池、封存留出和报告产物见[支撑/压力区策略有效性研究](support-resistance-effectiveness.zh-CN.md)。

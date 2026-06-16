# 趋势跟踪策略改进建议

本文基于当前仓库里的趋势跟踪实现整理改进方向。当前 `trend` 策略主要是日线双均线交叉，叠加成交量过滤、固定止损、ATR 止损/止盈、最大持仓数和单票仓位比例。核心实现分布在：

- `backend/src/services/strategy_registry.py`
- `backend/src/services/strategy_engine.py`
- `backend/src/services/backtest_engine.py`
- `frontend/src/components/StrategyForm.tsx`

## 1. 提高趋势信号质量

当前入场条件依赖快慢均线当日交叉，并要求成交量高于 `volume_multiplier * adv_20`。这个规则清晰，但容易在震荡区间反复触发，也缺少趋势强度确认。

建议优先补充：

- 趋势强度过滤：例如均线斜率、ADX、价格距长期均线的最小幅度、`ret_20d` / `ret_60d` 共振。
- 大盘环境过滤：例如只在 SPY/QQQ 处于上升趋势或市场宽度健康时允许新增仓位。
- 突破确认：把单日交叉扩展为 N 日确认，降低假突破和盘中噪声带来的交易频率。
- 多时间框架确认：日线入场前检查周线或更长窗口趋势，避免短周期反弹被误判为主趋势。

可落地位置：`strategy_registry.py` 增加参数默认值和归一化，`strategy_engine.py` 在 `_trend_following_handler` 中读取新的 daily feature 字段并参与 BUY 条件。

## 2. 完善股票池和流动性约束

当前如果股票池留空，趋势策略会默认解释为全部 active US common stock。这个默认行为方便跑通流程，但实际交易中会引入低流动性、过小市值、极端价格、停牌、刚上市或数据质量不足的标的。

建议补充：

- 最低价格、最低成交额、最低 ADV、最短上市天数过滤。
- 排除低价股、异常缺口股、近期停牌或特征缺失严重的标的。
- 支持按行业、市值、交易所、ETF/普通股等维度构建可复用股票池。
- 在回测结果中记录每次运行实际可交易股票池，便于复盘信号来源。

可落地位置：`stock_basket_service.py` 或 strategy universe 解析链路中增加可复用 universe filter；前端策略表单补充过滤配置。

## 3. 强化风险预算和组合约束

当前风险控制以单策略内的 `max_positions`、`position_size_pct`、固定止损、ATR 止损/止盈为主。它能控制单票风险，但还不足以处理组合层面的集中度、相关性和波动率。

建议补充：

- 波动率目标仓位：根据 ATR 或历史波动率动态调整单票仓位，而不是固定百分比。
- 组合级约束：总权益敞口、行业敞口、单主题集中度、单日新增仓位上限。
- 相关性控制：避免同时买入高度相关的一组股票，降低同源风险。
- 回撤熔断：账户或策略组合达到日内/累计回撤阈值时暂停新增仓位。
- 跟踪止盈：趋势策略常见收益来自少数大趋势，固定 ATR 止盈可能过早截断收益，可增加 trailing stop 或分批止盈。

可落地位置：`risk_manager.py`、`strategy_allocation_service.py`、`paper_trading_service.py` 和 backtest 买入/卖出执行链路。

## 4. 修正和澄清 ATR 参数语义

当前 `signal.atr_multiplier` 在趋势策略默认参数、表单和归一化逻辑中存在，但 `_trend_following_handler` 只把它写入 signal metadata；实际 ATR 止损和止盈使用的是 `risk.stop_loss_atr` 与 `risk.take_profit_atr`。

建议二选一：

- 如果 `signal.atr_multiplier` 表示信号过滤条件，就在信号层真正使用它，例如要求突破幅度超过某个 ATR 百分比。
- 如果它已经被 `risk.stop_loss_atr` 取代，就从表单文案、默认参数或兼容层中弱化它，避免用户误以为该字段会直接影响入场。

这是一个优先级较高的产品语义问题，因为它会影响用户对参数有效性的判断。

## 5. 提升回测真实性和评估维度

当前 backtest 已支持次日开盘成交、手续费、滑点、基准比较和最大回撤，但执行模型仍偏简化。趋势跟踪对交易成本、开盘跳空和持仓换手很敏感，建议提高回测假设透明度。

建议补充：

- 成交约束：按 ADV 或当日成交量限制最大参与率，模拟部分成交或无法成交。
- 开盘跳空风险：单独统计信号日收盘到次日开盘的 gap 对收益和止损的影响。
- 分市场阶段评估：牛市、熊市、震荡市分别输出收益、回撤、胜率、换手和超额收益。
- 参数稳健性：增加 walk-forward、out-of-sample、参数网格敏感性报告，避免只优化单一历史区间。
- 风险指标：补充 Sharpe、Sortino、Calmar、年化波动、平均持仓天数、最大连续亏损。

可落地位置：`backtest_engine.py` 的执行成本模型、summary metrics，以及前端 backtest 详情页。

## 6. 补齐信号解释、监控和审计

当前 signal metadata 已记录部分价格、ATR、仓位和配置，但还可以更系统地服务于复盘和上线监控。

建议补充：

- 对每个 BUY/SELL 记录完整判定依据：快线、慢线、前一日快慢线、成交量、ADV、趋势过滤项、市场过滤项。
- 记录被过滤的候选原因：例如成交量不足、趋势强度不足、大盘环境不允许开仓。
- paper trading 与 backtest 的信号一致性检查，确保同一交易日同一策略的信号原因一致。
- 策略运行监控：信号数量异常、交易失败、订单滑点异常、数据缺失、特征延迟等都应有告警。

可落地位置：`SignalEvent.metadata`、`paper_trading_scheduler.py`、paper trading 页面和 backtest 详情页。

## 建议优先级

1. 先处理 ATR 参数语义和信号 metadata，避免用户配置与实际行为不一致。
2. 再增加股票池/流动性过滤和市场环境过滤，通常能明显减少噪声交易。
3. 然后完善组合级风险预算和 trailing stop，让趋势收益更符合策略类型。
4. 最后扩展 walk-forward、参数稳健性和分市场阶段评估，作为上线前的研究验证工具。

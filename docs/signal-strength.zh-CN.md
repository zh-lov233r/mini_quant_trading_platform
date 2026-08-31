# BUY 信号强度

[English](signal-strength.md)

九个 engine-ready 策略大类都会计算确定性的、策略内部可比的 0–100 BUY 信号强度。强度用于比较同一策略、同一信号日产生的入场候选，不代表历史胜率，也不能跨策略大类直接比较。

## 执行语义

- `signal.min_strength_score` 的合法范围为 `[0, 100]`，默认值为 `50`。
- T 日数据决定分数、等级、阈值结果和确定性排名，这些值在下一交易日前冻结。
- 退出信号始终优先。通过阈值的 BUY 入场按 `strength DESC, instrument_id ASC, symbol ASC` 在下一有效交易日开盘执行，直到现金或 `risk.max_positions` 用完。
- 某候选缺少开盘价时跳过并尝试下一名；T+1 价格不能改变已经冻结的排名。
- 低于阈值的信号仍保留在 full 回测和 Paper run 审计数据中，但不能开仓。

等级固定为：50 以下 `weak`，50 起 `medium`，70 起 `strong`，85 起 `very_strong`。API 通过 `SignalRecord.strength` 返回分数、等级、阈值、通过状态、排名、模型版本和加权组成项；旧回测返回 `null`。

## 各大类公式

每个组成项使用以下一种截断归一化，最终加权分数四舍五入到两位：

```text
rise(value, gate, cap) = 100 × clamp((value - gate) / (cap - gate), 0, 1)
fall(value, gate, ideal) = 100 × clamp((gate - value) / (gate - ideal), 0, 1)
```

| 大类/setup | v1 组成与归一化 |
|---|---|
| 趋势 | 均线分离度/ATR 60%、交叉冲量/ATR 20%，均用 `rise(0, 0.5)`；量比 20%，用 `rise(volume_multiplier, 2 × volume_multiplier)`。 |
| 均值回归 | `abs(zscore)` 100%，用 `rise(zscore_entry, 2 × zscore_entry)`。 |
| 动量突破 | 20 日收益 40%、SMA20 上方延伸 35%、量比 25%；均从配置门槛到两倍门槛归一化。 |
| 岛形衰竭 | 向下缺口 60%、缩量质量 40%；配置的缺口和量比上限作为门槛。 |
| 岛形突破 | 左缺口 30%、右缺口 40%、突破量比 30%；均从各自配置下限到两倍下限归一化。 |
| 岛形回踩 | 左缺口 15%、右缺口 20%、突破量比 20%、回踩缩量 25%（`fall(retest_volume_ratio_max, 0)`）、ATR 站稳距离 20%（`rise(0, 1)`）。 |
| 双底 | 按阶段组合双底对称性、反弹质量、缩量、右侧站稳、突破量比和颈线延伸；最终突破阶段四项各占 25%。 |
| 头肩底 / 圆弧底 / V 型反转 | 结构质量、价格确认、成交量质量和阶段确认各占 25%。 |
| 支撑反弹 | ATR 确认幅度 70%（门槛到两倍门槛）与盈亏比 30%（`min_reward_risk` 到其两倍）。 |
| 压力突破 | ATR 确认幅度 45%、量比 35%、盈亏比 20%，均从配置门槛到两倍门槛归一化。 |
| 突破回踩 | ATR 站稳幅度 35%（0 到 `bounce_confirmation_atr`）、回踩缩量 35%（`retest_volume_ratio_max` 到 0）、盈亏比 30%（配置下限到其两倍）。 |

支撑/压力策略现有 Beta 后验继续保存在原始 `score` 和 `score_evidence` 中作为审计证据，不参与 v1 候选选择或强度排名。

所有公式输入在 T 日收盘前可得。必需输入缺失或不是有限数时，运行明确失败，不会静默退回股票代码顺序。

## 持久化与兼容

策略原始 `signals.score` 保留不变，统一强度记录写入 `signals.features.strength`，因此不需要数据库 schema 迁移。缺少阈值的现有策略 JSON 会标准化为 `50`；历史信号不会回填。

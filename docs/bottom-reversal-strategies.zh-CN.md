# 底部反转策略

[English](bottom-reversal-strategies.md)

五类日线多头策略共用当前 C++ 形态内核，算法 revision 均为 2：`island_reversal`、`double_bottom`、`head_shoulders_bottom`、`rounded_bottom`、`v_reversal`。回测和 Paper 日评估共用判定；新实例默认 `draft`。新增阈值是可测试的初始量化定义，尚未研究验证为最优参数，规则测试不代表盈利验证。

## 数据、时序与累计仓位

- 成交量倍数为 `volume / volume_sma_20`；反弹段均量比为各交易日量比的算术均值。K 线实体为 `abs(close-open) / ATR14`。平台及整理区的区间、首尾位移使用窗口末日 ATR14 归一化。依赖字段缺失时，相应条件不成立。
- 沿用行情准备层的复权价格口径（优先前复权，缺失时使用既有未复权回退），不改变成交价格、成本、滑点或拆股处理。Pivot 必须等待右侧确认 K 线完整；图上的 Pivot 日期是极值日，信号日期是确认日。
- 突破要求**收盘严格高于关键位乘以 `(1 + breakout_buffer_pct)`，且量能达标**。岛形使用真实向上跳空阈值；缩量回踩本身不要求放量。T 日收盘产生信号，下一有效交易日开盘成交；缺失下一有效日开盘价的候选按既有执行规则跳过。
- 累计仓位默认 `risk.stage_1_target_pct=0.20`、`stage_2_target_pct=0.50`、`stage_3_target_pct=1.00`，必须 `0 < 第一阶段 < 第二阶段 < 第三阶段 = 1`。目标市值为 `当前组合权益 × position_size_pct × 当前阶段目标`。
- 允许直接进入后期阶段，但基础形态必须成立；同日同一形态取最高阶段。圆弧底第三次及以后合格回踩、V 型后续追涨均只补足第二阶段目标，已经达到目标则不买入。资金不足或部分成交后，后续同阶段信号可以补足剩余目标，不回退阶段。
- SELL 先于 BUY；同一 setup 加仓不占新持仓名额。保留幂等订单、逐批成交、加权成本和通用止损止盈。策略退出均为整仓退出。

## 原文要求与量化定义

| 策略 / 原文要求 | 可执行定义及阶段 | 参数与回归测试 |
|---|---|---|
| 岛形：下跌末端、长阴后小实体岛区、放量长阳跳出 | 第一阶段为缩量向下缺口，前根须阴线且实体 ≥0.5 ATR，跳空阴线实体 ≤1 ATR；其他岛内 K 线实体 ≤0.5 ATR，不强制交替阴阳。第二阶段为真实向上缺口、阳线实体 ≥0.5 ATR 且放量。第三阶段为缩量缺口回踩守住支撑。必须满足窗口跌幅，低于 SMA50 不能代替下跌背景。 | `previous_body_atr_min`、`breakout_body_atr_min`、`exhaustion_body_atr_max`、`island_body_atr_max`；`test_island_*` 覆盖方向、实体、量能、缺口、岛内回补、失守。 |
| 双底：第二底不创新低，反弹温和放量，右侧确认 | 右底位于 `[左底, 左底×1.03]`；上涨日占比在左底之后至颈线高点（含）计算，默认 ≥60%，该段平均量比 1.0–1.5。第一阶段为右底确认。第二阶段须在右底后 `retest_window` 内，反弹段温和放量后出现缩量回踩；成交量 ≤前反弹段最大日量×`retest_volume_ratio_max`。第三阶段放量收盘突破颈线，可跳过回踩。 | `bottom_tolerance_pct` 只允许向上容差；`rebound_up_day_ratio_min`、`rebound_volume_ratio_min/max`、`breakout_buffer_pct`；`test_double_bottom_*` 覆盖 98/96.5 拒绝、相等及较高右底、盘中假突破、量能及时间窗。 |
| 头肩底：左肩平台、缩量头部、回到平台、缩量右肩 | 每个阶段重新检查下跌背景、头部深度与缩量、左肩平台。平台为头部前包含左肩的连续 5 根，区间 ≤3 ATR、首尾收盘位移 ≤1 ATR，多组时选结束最近一组。头部后反弹收盘落在平台高低价格区间内，至反弹高点平均量比 1.0–1.5，再判断缩量右肩及动态颈线突破。三阶段为头部候选、右肩、颈线突破。 | `platform_bars`、`platform_range_atr_max`、`platform_drift_atr_max`、`rebound_volume_ratio_min/max`；`test_head_*` 验证后期不能绕过基础条件及同日最高阶段。 |
| 圆弧底：右侧反复放量上攻、缩量更高回踩、走弱离场 | 保留 80–240 日对数收盘价二次拟合。每次回踩前的反弹必须有收盘上涨且量比达标的上攻；相邻合格回踩低点及对应反弹高点均抬高。第一次阶段 1，第二次及以后阶段 2；至少两次后才能放量收盘突破碗口至阶段 3。阶段 3 前，已确认高点较前峰低 ≥0.5%，随后收盘跌破中间已确认回踩低点至少 0.5%，且期间未重新放量突破前峰，整仓退出。 | `right_volume_ratio_min`、`pullback_volume_ratio_max`、`weakening_buffer_pct`；`test_rounded_*` 验证第三、四次回踩及通用止损前走弱退出、正常结构不误退。 |
| V 型：急跌后的放量转折、连续上涨、横盘突破回踩、巨量阴线 | 放量反转允许在最低点当日或随后两根形成，保留反弹幅度及 ATR 门槛，之后不得破底。连续上涨从转折收盘逐日比较。整理 3–10 根，区间 ≤3 ATR、首尾位移 ≤1 ATR，选择最长合格窗口。放量突破收盘高于顶部 0.5%；回踩低点在顶部 ±2%，收盘 ≥顶部、缩量，中间收盘不能低于容差下沿。有效顶部突破前，前两日连续放量上涨后出现实体 ≥0.5 ATR 的巨量阴线才触发专用退出。 | `pivot_max_bars`、`consolidation_range_atr_max`、`consolidation_drift_atr_max`、`breakout_buffer_pct`、`bearish_body_atr_min`；`test_v_*` 覆盖顶部 98.5 而低点 105 的假回踩、真回踩、失守、延迟转折与阴线退出。 |

## 新增高级参数契约

创建向导与编辑表单均提供以下字段；头肩底、圆弧底和 V 型还开放其余完整信号参数。API 的五类详细 schema 与 native descriptor 同步。上下限组合必须有序；整数窗口必须为正。百分比在 JSON 中使用小数，界面使用百分数。

| 策略 | `signal` 参数 | 默认值 | 范围 |
|---|---|---:|---|
| `island_reversal` | `previous_body_atr_min` | 0.5 | >0 |
| `island_reversal` | `breakout_body_atr_min` | 0.5 | >0 |
| `island_reversal` | `exhaustion_body_atr_max` | 1.0 | >0 |
| `island_reversal` | `island_body_atr_max` | 0.5 | >0 |
| `double_bottom` | `rebound_volume_ratio_min` | 1.0 | >0 |
| `double_bottom` | `rebound_volume_ratio_max` | 1.5 | >0 |
| `head_shoulders_bottom` | `platform_bars` | 5 | integer ≥3 |
| `head_shoulders_bottom` | `platform_range_atr_max` | 3.0 | >0 |
| `head_shoulders_bottom` | `platform_drift_atr_max` | 1.0 | >0 |
| `head_shoulders_bottom` | `rebound_volume_ratio_min` | 1.0 | >0 |
| `head_shoulders_bottom` | `rebound_volume_ratio_max` | 1.5 | >0 |
| `rounded_bottom` | `weakening_buffer_pct` | 0.005 | (0, 1) |
| `v_reversal` | `consolidation_range_atr_max` | 3.0 | >0 |
| `v_reversal` | `consolidation_drift_atr_max` | 1.0 | >0 |
| `v_reversal` | `breakout_buffer_pct` | 0.005 | [0, 1) |
| `v_reversal` | `bearish_body_atr_min` | 0.5 | >0 |

## 审计与生命周期图

沿用信号审计 JSON，不新增审计表或端点。`setup` 包含形态、稳定 setup ID、阶段、累计目标、失效价与 anchors；新增实体量比、反弹均量比、左肩平台起止及高低区间、圆弧反弹峰值、V 型整理区起止及边界。走弱 SELL 记录前峰、较低高点、失守回踩低点和退出日期；图中展示相应确认点与中英文退出原因。信号包含原始观测值，图表只展示当时已确认结构。 拥挤的右边缘标注优先向左或换行显示，避免裁切退出原因。

## 验证与操作边界

`backend/tests/test_bottom_reversal_rules.py` 为正反例入口，每个正例断言动作、阶段、日期与关键价格，逐日截断回测检查未来后缀不会改写过去信号。`test_native_backtest_kernel.py` 覆盖下一有效日开盘、缺失开盘、成本、现金限制、累计目标及拆股阶段衔接；`test_paper_trading_service.py` 使用内存数据库和模拟券商验证部分成交。前端 `patternLifecycle.test.ts` 与 `BottomReversalFields.test.ts` 检查标注、参数及双语显示。

五类 golden 的审计字段变化来自新增观测量及 revision；双底短样本的反弹量调整到 1.0–1.5、回踩窗口调整为 5，头肩底短样本显式使用 3 根平台，生产默认仍是 5。五类现有样本的金融摘要及成交阶段保持一致；新增审计字段与确认评分改变信号及嵌套持仓元数据的哈希。圆弧样本第二回踩日先触发通用止盈，SELL 仍携带阶段 1，因此不能把该退出误认作第二次建仓；新增独立正例验证阶段 2 与后续重复补仓。其他四类策略的完整账本 golden 保持不变。

通用退出仍为形态失效、最大亏损、ATR 止损或止盈；新增专用退出只作用于最终确认前。原文的基本面持股建议、消息解释和收益判断仅作说明，没有新增自动条件或数据源。此次变更无需数据库迁移、重置或历史改写；不会启动调度、激活组合或提交券商订单。收益研究需单独进行，结论标记为 `validated`、`not_validated` 或 `inconclusive`。

# 支撑区/压力区策略审查（pivot-slope-regime-v3）

**日期**：2026-09-04
**审查对象**：`backend/native/src/support_resistance_core.cpp`、`backend/native/src/support_resistance_core.hpp`、`backend/native/src/backtest_kernel.cpp`、`docs/support-resistance-strategy.zh-CN.md`、`docs/support-resistance-effectiveness.zh-CN.md`
**结论摘要**：工程质量（因果性、稀疏版本化、审计事件、验证协议）扎实；但策略逻辑存在若干结构性问题，会使其实际退化为一个远比文档描述简单、且交易机会极少的单模式策略。

> 本文档仅为代码与逻辑审查，不构成盈利能力判断或实盘安全性证明。

---

## 目录

1. [致命：每个 kind 只保留一条线，导致两个模式实际失效](#一致命每个-kind-只保留一条线导致两个模式实际失效)
2. [reward/risk 与 strength score 退化](#二rewardrisk-与-strength-score-退化)
3. [几何与检测层面的不一致](#三几何与检测层面的不一致)
4. [风控与执行](#四风控与执行)
5. [统计层面](#五统计层面)
6. [优先级建议](#六优先级建议)
7. [建议的先验证步骤](#七建议的先验证步骤)

---

## 一、致命：每个 kind 只保留一条线，导致两个模式实际失效

### 现象

`rebuild_zones` 在结尾对每个 `source_kind` 只保留质量最高的一条：

```cpp
// support_resistance_core.cpp:1724
if (!zones.empty()) selected.emplace(zones.front()->zone_key, *zones.front());
```

因此任一交易日、任一标的最多只有 **2 个 zone**（一条低点线 + 一条高点线）。

### 连锁后果

#### 1.1 `breakout_retest` 在生产中不可达

- 突破当日 `apply_current_bar_zone_state`（`:1462`）把该 zone 的 role 翻成 `Support`。
- `build_entry_channel`（`:708`）要求上方存在 `role == Resistance` 的 zone 才能构成有效通道。
- 此时唯一的高点线已变成 support，低点线在价格下方 → `missing_support_or_resistance` → 通道无效 → 候选必被拒。
- 唯一的理论出口（低点线跑到高点线上方）会被 `classify_market_regime` 判为 `unordered_boundaries` → `transition` → 禁止买入。

**验证证据**：`backend/tests/test_support_resistance_strategy.py::test_retest_can_trigger_when_direct_breakout_entry_is_disabled` 是通过**手工往 `state.zones` 注入一个 `new-resistance`** 才通过的。真实的 `rebuild_zones` 永远无法同时产出"被突破的旧压力线"和"上方的新压力线"，因为高点线的槽位只有一个。

#### 1.2 实际只剩一个可交易模式

叠加 `resistance_breakout` 被固定拒为 `direct_breakout_audit_only`（`apply_regime_entry_policy`），三个模式中**只有 `support_bounce` 在真正交易**。

这与以下内容存在系统性认知偏差：

- `docs/support-resistance-strategy.zh-CN.md` 描述的三模式设计；
- `support_bounce_enabled` / `resistance_breakout_enabled` / `breakout_retest_enabled` 三个配置项与"至少开启一种"的校验；
- `docs/support-resistance-effectiveness.zh-CN.md` 中按三个模式分别做 discovery（48 trial × 3）的有效性研究预算分配——其中两路实际在测量不可能成交的东西。

#### 1.3 `role` 粘性 + 单槽位 = 上涨趋势自毁进场通道

```cpp
// support_resistance_core.cpp:1650
if (matched != nullptr) role = matched->role;
```

`match_zone` 按几何重叠匹配，因此**role 在每日 rebuild 中是粘性的**：价格只要有一次收盘越过压力区上沿，这条高点线就永久变成 support，直到拟合几何漂移到不再与旧 zone 重叠才可能重置。

而 `uptrend` 恰恰是唯一允许 `support_bounce` + `breakout_retest` 的 regime。**上涨趋势中最容易发生的向上超越，正好会摧毁进场通道所必需的压力边界**。这构成一个近似吸收态：越是趋势健康，越买不进去。

### 建议

| 改动 | 说明 |
|---|---|
| 每个 kind 保留 top-N 条线（N = 3~5） | 排序键沿用现有的 `pivot_count / recency_weight / fit_residual_atr / 距现价距离 / zone_key`，只是不再截断到 1 |
| role 从"粘性状态"改为每日按价格位置重算 | 或至少允许 role 双向反转，而不是只在 `close < zone.lower` 时才翻回来 |
| 通道用几何位置而非 role 选边 | `build_entry_channel` 改为"收盘价下方最近的边界 / 上方最近的边界"，与 zone 的 role 标签解耦 |

> 注意：多 zone 会增加稀疏版本表的行数，需要重新评估 `plan_support_resistance_materialization.py` 的估算与 COPY 批量写入的规模。这属于必要代价。

---

## 二、reward/risk 与 strength score 退化

### 2.1 target 几乎恒为 `entry + 3·ATR`

```cpp
// support_resistance_core.cpp:1170
const double target = overhead.empty()
    ? entry + config.take_profit_atr * bar.atr_14
    : overhead.front();
```

`overhead` 收集的是"上方其他 role == Resistance 的 zone 的下沿"。由于第一节所述只有 2 个 zone，**`overhead` 几乎恒为空**，target 退化为固定的 3 ATR。

### 2.2 risk 几乎恒为 ~1.25 ATR

`stop = max(zone.lower, entry - 1.5·ATR, entry × 0.92)`。

对 support_bounce：`close ≥ zone.upper + 0.25·ATR`，zone 半宽 0.5 ATR，因此

```
zone.lower ≈ entry - 0.25·ATR - 1.0·ATR = entry - 1.25·ATR
```

它几乎总是三者中最高（最紧）的一个 → 被 `max` 选中。

### 2.3 结果：R/R 恒为 ~2.4，strength score 退化为单变量

- `min_reward_risk = 1.5` 这道闸门**几乎从不触发**。
- `reward_risk` 强度分量恒等于 `100 × (2.4 - 1.5) / (3.0 - 1.5) = 60` 分。
- support_bounce 的 score = `0.7 × conf_norm + 0.3 × 60`。

于是整个 signal strength **退化成 `confirmation_atr` 的单变量函数**。`min_strength_score = 50` 的实际效果，只是把 bounce 确认阈值从 0.25 ATR 悄悄提高到约 0.36 ATR：

```
0.7 × conf_norm + 18 ≥ 50  ⟹  conf_norm ≥ 45.7  ⟹  confirmation_atr ≥ 0.364·ATR
```

### 2.4 副作用：跨标的名额分配产生逆向选择

```cpp
// backtest_kernel.cpp:2206
std::sort(buys.begin(), buys.end(), ... -left->strength_score ...);
// backtest_kernel.cpp:2226
} else if (positions.size() >= static_cast<std::size_t>(strategy.max_positions)) { continue; }
```

`max_positions = 10` 的名额按 `strength_score` 抢占。而该分数单调于"收盘离支撑上沿多远" → 在候选较多的交易日，系统会**系统性地挑走离支撑最远、进场价最差、最追高的那一批**。这是明确的逆向选择。

### 2.5 `touch_count` 被记录但从不参与决策

`touch_count`（同一价位被测试并守住的次数）是最经典的 S/R 质量指标，目前只写进事件与 zone 版本，不进入任何评分或过滤。而且它的初始值是 `max(inliers.size(), matched->touch_count)`，即**在没有任何真实触碰之前就已经是 3+**，语义上也不干净。

### 建议

- 让 score 恢复多维信息，至少加入：zone 的 `pivot_count` / 真实 `touch_count`、`fit_residual_atr`（拟合质量）、bounce 当日的成交量确认、**距离支撑的"近"而非"远"**、相对大盘/行业的强弱。
- `touch_count` 与 `pivot_count` 拆分为两个独立字段，前者只在真实触碰事件时递增。
- 把 target 的候选来源扩大（配合第一节的多 zone），让 R/R 重新具备区分度。
- R/R 计算中扣除手续费与滑点后再与 `min_reward_risk` 比较。

---

## 三、几何与检测层面的不一致

### 3.1 内点容差比 zone 半宽更宽

- `line_inlier_tolerance_atr = 0.75`（上下共 1.5 ATR）
- `zone_half_width_atr = 0.5`（上下共 1.0 ATR）

**定义这条线的 pivot 本身可以落在 zone 外面**——一个"支撑区"竟然不包含它自己的支撑触点。

**建议**：半宽由实际残差推导，例如 `half_width = max(0.5·ATR, k · fit_residual_atr · ATR)`，或直接令半宽 ≥ 内点容差。

### 3.2 半宽终身冻结

半宽在 zone 创建时按当日 ATR 计算并冻结，之后 `project_zone` 只做平移。高波动期建立的 zone 在平静期依然过宽，反之过窄。可考虑按当前 ATR 与创建时 ATR 的比值做有界缩放（同时保留 anchor 语义以维持稀疏版本化）。

### 3.3 混用两种 ATR 口径

- 内点容差用**每个 pivot 各自的 ATR**（`pivot.pivot->atr`）
- 半宽用**当日 bar 的 ATR**（`bar.atr_14`）
- 斜率上限用**内点的加权中位数 ATR**（`representative_atr`）

三种口径混用，建议统一或至少在文档中明示理由。

### 3.4 四状态分类的"四重确认"实为约两重

```cpp
// support_resistance_core.cpp:319
std::string pivot_direction(const std::vector<const Pivot*>& pivots, double half_width_ratio) {
    const Pivot& previous = *pivots[pivots.size() - 2U];
    const Pivot& latest = *pivots.back();
    ...
}
```

`pivot_direction` 只取该 zone inliers 的**最后两个** pivot，而这些 pivot 按定义就贴在拟合线上（残差 ≤ 0.75 ATR）。因此 `pivot_direction` 与 `boundary_direction` 高度共线，所谓"上下边界方向 + 上下 pivot 结构四者全部一致"其实只有约 2 个独立证据。

但用 **AND 连接四个条件**的代价是真实的：`uptrend` / `downtrend` 会非常罕见，绝大多数交易日落入 `transition`（禁止买入）。这可能是交易频率过低的主要来源。

**建议**：

- 用更多 pivot（例如最近 3~4 个）判断结构方向，或直接用独立于拟合线的证据（如摆动高低点序列、ADX、相对位置）；
- 或把硬 AND 改为打分（4 项中至少 3 项一致即可，并把不一致数量记入证据）。

### 3.5 pivot 确认要求严格唯一极值

```cpp
// support_resistance_core.cpp:~1520
if (price != extreme || std::count(values.begin(), values.end(), extreme) != 1) continue;
```

要求极值在 7 根 K 线窗口内**浮点严格唯一**。低价股、最小变动价位聚集、横盘整理期会静默丢弃大量合法 pivot，而 `min_line_pivots = 3` 又很紧（120 日窗口内至少 3 个内点）。

**建议**：改为容差判定（`|price - extreme| ≤ ε·ATR`，ε 取 0.05~0.1），并列时取最早的一根。

### 3.6 次要项

- `recency_weight` 被赋值为 `fit->total_weight`（`:1694`），这是内点衰减权重之和，本质上是"数量 × 新近度"的混合量。排序时第二键 `-recency_weight` 与第一键 `-pivot_count` 高度冗余。
- `stored_zone_price()` 被同时用于价格、斜率和无量纲残差 `fit_residual_atr`。数值上无害，但语义混淆（价格量化规则套用到非价格量上）。

---

## 四、风控与执行

### 4.1 `max_loss_pct = 0.08` 不是真正的最大亏损

退出判定是 `bar.close < stop`，成交在 T+1 开盘。**跳空风险完全敞开**，单笔实际亏损可以远超 8%。

**建议**：改名为 `stop_reference_pct` 之类，或补充跳空处理 / 盘中止损语义，并在有效性报告中单列"退出日跳空分布"。

### 4.2 止损不随支撑线上移

```cpp
// resolve_exit
if (const JsonObject* zone = get<JsonObject>(*frozen, "zone")) {
    zone_line = number(*zone, "lower");
}
```

用的是信号日**冻结**的 `zone.lower`，不按 `slope_per_session` 前推。这与进场时通道必须投影一个交易日的逻辑自相矛盾。在上升通道里，止损会随时间越来越松，且与"支撑线本身已经上移"的事实脱节。

**建议**：`zone_line = anchor_lower + slope × (今日 session_index - anchor_session_index)`，与 `project_zone` 保持一致。同时考虑加入保本止损（浮盈达 1 R 后止损上移至成本）。

### 4.3 仓位大小完全不用已算好的止损距离

```cpp
// backtest_kernel.cpp:2236
const double desired = equity_before * strategy.position_size_pct * target_fraction;
```

固定 10% 名义仓位，`max_positions = 10`。策略已经算出了 `stop_price` 和 `reward_risk`，却完全不用于定量。**宽 ATR 的标的单笔风险可以是窄 ATR 标的的数倍**。

**建议**：改为按风险定量

```
qty = (equity × risk_per_trade_pct) / (entry - stop)
```

并对单标的名义仓位设上限。这是本文档中投入产出比最高的一项改动。

### 4.4 同一 zone 没有冷却期

`detect_candidates` 的 bounce 条件是 `previous_close > zone.upper && bar.low <= zone.upper && bar.close ≥ zone.upper + 0.25·ATR`。这个条件**可以连续多日成立**，止损出场后第二天同一条支撑立刻可以再次触发——典型的锯齿绞肉。

**建议**：zone 被止损后拉黑 N 个交易日，或直到该 zone 的 pivot 成员发生变化（有新 pivot 确认）才恢复候选资格。

### 4.5 T+1 开盘通道复核造成双尾截断

`record_execution_rejection` 的机制会拒掉两类跳空：

- **向上跳空出通道上沿** → 拒单。这恰恰砍掉的是 bounce setup 中最好的那批跟进行情。
- **向下跳空跌破支撑内沿** → 拒单。这一侧是有益的。

净效果是一个隐蔽的**负向选择偏差**：留下的都是不温不火的开盘。

**建议**：在有效性报告中单列"被拒单信号的后续 1/5/10/20 日收益"，量化这个偏差的方向和幅度，再决定是否放宽上沿（例如允许在压力内沿之上 x·ATR 内成交，或改用限价单挂在内沿）。

### 4.6 没有市场级过滤

目前只有 per-symbol 的四状态分类。指数暴跌时个股支撑会批量失效——这是这类策略最典型的爆仓模式，而 `max_positions = 10` 且无行业上限，会同时持有 10 个同向暴露。

**建议**：加入大盘 regime 过滤（如指数在 200 日均线下方时禁止新开仓，或按大盘状态缩减 `position_size_pct`），以及行业/相关性上限。

---

## 五、统计层面

### 5.1 Beta posterior 的样本严重自相关

`advance_symbol` 对**每一个候选**（包含被 regime / 通道拒绝的、以及连续多日重复触发同一 zone 的）都推入 `pending_outcomes`：

```cpp
for (const Candidate& candidate : candidates) {
    ...
    state.pending_outcomes.push_back(PendingOutcome{...});
}
```

20 个交易日的结局窗口大量重叠，Beta 后验把强自相关样本当作独立样本，**置信度被严重高估**。

### 5.2 标注规则与实际交易规则不一致

| | 结局标注 | 实际交易 |
|---|---|---|
| 目标 | `+3.0 ATR` | `+3.0 ATR` 或最近压力下沿 |
| 止损 | `-1.5 ATR` | `zone.lower`（≈ -1.25 ATR）、-1.5 ATR、-8% 三者取最紧 |
| 时限 | 20 个交易日 | 40 个交易日 |
| 进场 | 信号日收盘 | T+1 开盘含滑点，且可能被通道拒单 |

即"统计的东西"与"交易的东西"不是同一件事。

### 5.3 censored 被排除在分母外

`posterior() = (wins + 1) / (wins + losses + 2)`，censored 不进分母。而"20 日内两边都没碰到"是有信息量的观测，直接丢弃会把后验推向两个极端。

**结论**：目前该后验不参与候选排序（文档已明确说明只作形态统计证据），因此暂时无害。**但在修复以上三点之前，不要把它接入任何决策路径。**

---

## 六、优先级建议

### P0 —— 解开死结（不做则其余优化意义有限）

1. **每个 `source_kind` 保留 top-N 条线**（`support_resistance_core.cpp:1724`）
2. **role 每日按价格位置重算 / 允许双向反转**（`:1650`）
3. **`build_entry_channel` 改为按几何位置选边，与 role 解耦**（`:708`）

> 一次性解开"三个模式实际只剩一个"和"上涨趋势自毁进场通道"两个死结。

### P1 —— 恢复信号的信息量

4. target 候选来源扩大，让 `reward_risk` 重新有区分度（`:1170`）
5. strength score 加入独立维度（拟合质量、真实 touch_count、成交量、距支撑的近度、相对强弱）
6. 仓位改为按 `entry - stop` 定风险（`backtest_kernel.cpp:2236`）

### P2 —— 稳健性与一致性

7. 止损随支撑线投影上移；加入保本止损
8. zone 止损后冷却期
9. 半宽与内点容差口径统一，半宽随残差自适应
10. pivot 唯一极值判定改为容差判定
11. 加入大盘 regime 过滤与行业/相关性上限

### P3 —— 分类与统计

12. `pivot_direction` 使用更多 pivot 或改为打分制，降低 transition 占比
13. Beta 后验去重叠采样、标注规则对齐实际交易规则、censored 纳入处理
14. 文档与配置项同步说明"三模式中实际可交易的是哪些"

---

## 七、建议的先验证步骤

上述结论中有若干是从代码推断出的"应当退化"，建议先用只读脚本在现有回测/物化数据上做一次事实核对，再决定改动范围：

1. **四状态分布**：各 regime 的覆盖天数占比。若 `transition` > 60%，可确认 §3.4 的路径过窄。
2. **各 setup 的漏斗计数**：candidate → regime_eligible → channel_eligible → entry_eligible → 实际成交。预期可见 `breakout_retest` 在 `entry_eligible` 一列恒为 0。
3. **`overhead` 为空的比例**：直接验证 §2.1 中 target 恒为 3 ATR 的推断。
4. **`reward_risk` 与 `strength.score` 的分布**：若两者方差极小，§2.3 成立。
5. **`entry_channel_rejection` / `execution_rejection` 的原因码分布**，以及被拒信号后续 1/5/10/20 日收益（验证 §4.5 的双尾截断偏差）。
6. **stop 触发日的跳空分布**：验证 §4.1 中 8% 不是真实上限。

这些统计都可以从现有的 `support_resistance_run_events` 与 `support_resistance_regime_versions` 表只读聚合得到，不需要重跑回测。

---

*本文档为代码与逻辑层面的研究证据，不构成盈利能力判断，也不授权任何 allocation 激活、scheduler 启动或订单提交。*

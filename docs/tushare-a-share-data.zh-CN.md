# Tushare A 股数据

[English](tushare-a-share-data.md)

本流程把 Tushare 的沪深北 A 股证券主数据、未复权日线和复权因子写入现有 PostgreSQL 行情表，再复用项目的 `daily_features` 计算与共享 C++ 九策略回测内核。它只支持研究和回测，不启动 backend、paper scheduler，不创建策略 allocation，也不调用 Alpaca。

## 凭证与权限

将 Tushare Pro token 只保存在根目录 `.env`：

```env
TUSHARE_TOKEN=...
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname
```

`.env` 已被 Git 忽略。不要把 token 放进命令行、源码、日志或提交。Tushare 当前文档说明 `stock_basic` 和 `adj_factor` 通常要求至少 2000 积分；权限或限频不足时导入器会保留已提交日期并停止，修复后可幂等重跑相同区间。

## 安全执行

先只读核对数据库、现有 Tushare 行数、日期和股票范围：

```bash
make import-a-share A_SHARE_ARGS="plan --start-date 2016-01-01 --end-date 2026-09-02"
```

确认后导入：

```bash
make import-a-share A_SHARE_ARGS="apply --start-date 2016-01-01 --end-date 2026-09-02"
```

正常全量导入也会维护上证指数（`000001.SH`）和深证成指（`399001.SZ`）。只修复或更新这两个指数时运行：

```bash
make import-a-share A_SHARE_ARGS="apply --indices-only --start-date 2016-01-01 --end-date 2026-09-02"
```

先验证单只股票或补一个小窗口时可重复传 `--ts-code`：

```bash
make import-a-share A_SHARE_ARGS="apply --start-date 2026-09-01 --end-date 2026-09-02 --ts-code 000001.SZ"
```

导入按上交所开放日逐日执行并逐日提交 EOD 数据；网络或权限故障后直接重跑相同命令，不要删除历史。默认每次请求至少间隔 0.2 秒，可用 `--request-interval-seconds` 调低速率。`--skip-features` 只用于诊断，使用后数据尚不能回测。

## 数据口径

- 证券代码保留 Tushare 后缀，例如 `000001.SZ`、`600000.SH`、`920000.BJ`；交易所保存为 `XSHE`、`XSHG`、`XBSE`，币种为 `CNY`，locale 为 `cn`。
- `daily.vol` 的单位是“手”，导入时乘以 100 保存为股；`daily.amount` 的单位是千元，据此计算未复权 VWAP。
- 日线时间戳使用交易日上海时间 15:00 并转换成 UTC。现有列名 `dt_ny` 是历史命名，但该时间戳在纽约时区仍落在同一公历日期，所以主键交易日与 Tushare `trade_date` 一致。
- 前复权采用 Tushare 官方公式 `原始价格 × 当日复权因子 / 数据库中该标的最新复权因子`，后复权采用 `原始价格 × 当日复权因子`。复权因子刷新会改变数据指纹并使 PreparedDataset 缓存失效。
- 导入器会拒绝非有限、非正价格、倒置 OHLC、负成交量/成交额、缺失复权因子以及达到 6000 行上限的疑似截断响应。
- `backfill_adjusted_prices.py` 不会覆盖 `vendor='tushare'` 的供应商复权字段。
- 两个大盘指数以 `INDEX` 类型存储。指数日线不需要公司行动复权，因此前后复权因子均持久化为 `1.0`；仍生成日频特征以保持行情完整性约束。

## 九策略回测

导入结束会同步 active 的 `All A Shares (Tushare)` 股票组合。在 `/backtests` 选择任意 engine-ready 策略，再用该股票组合覆盖策略自带 universe；九种类型均走同一原生数据集和回测入口：

`trend`、`mean_reversion`、`momentum_breakout`、`island_reversal`、`double_bottom`、`head_shoulders_bottom`、`rounded_bottom`、`v_reversal`、`support_resistance`。

回测继续遵守 T 日收盘产生信号、下一有效交易日开盘成交、SELL-first、共享现金和确定性排序。A 股股票池会把空值、`SPY` 或 `QQQ` 基准自动替换为上证指数；结果页显示上证指数与深证成指，不再显示 SPY 或 QQQ。非 A 股回测仍显示 SPY 和 QQQ。

当前完成的是 A 股数据与九策略执行兼容，不是完整的 A 股交易所撮合仿真：100 股买入单位、涨跌停不可成交、停牌排队、印花税差异等尚未自动建模。可用手续费和滑点参数做保守成本压力，但不得把结果描述为实盘安全性或盈利证据。

## 验证

导入后应核对 Tushare 标的的 EOD/feature 数量相等、复权字段非空，并用九策略聚焦测试验证市场代码无硬编码：

```bash
PYTHONPATH=backend .venv/bin/python -m unittest \
  backend.tests.test_import_tushare_a_share \
  backend.tests.test_native_nine_strategy_golden.NativeNineStrategyGoldenTests.test_all_nine_accept_a_share_symbols_and_exchanges
```

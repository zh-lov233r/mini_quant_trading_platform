# 回测取数链路优化计划

日期：2026-09-03 · 基线提交：`b4e42e1`（含未提交改动）
测量脚本：`backend/utils/explain_backtest_feature_query.py` · 原始输出：`tmp/explain_feature_query.txt`

> 本文按 AGENTS.md 对「带日期的交付报告」的豁免，只提供中文版。

---

## 0. 一句话结论

188 秒的冷建里，**约 130 秒在 Python 和线路上，不在 Postgres 里**。因此优先级是「先修客户端与传输，再修 SQL」，而不是反过来。C++ 内核只占 1.78 秒，任何阶段都不应该去加计算线程。

---

## 1. 实测基线

窗口 `2025-01-01 ~ 2026-07-31`，universe 5,555 只 A 股，**2,068,420 行**（requested 口径）：

| 项 | 实测 | 说明 |
|---|---:|---|
| `COUNT(*)` 预飞 | 4.59 s | 与主查询同样的 join，产出只有一个整数 |
| **V0 服务端执行** | **33.37 s** | `EXPLAIN ANALYZE`，不含返回行 |
| **传输 + 驱动** | **82.82 s** | 流式读 116.19 s 减去服务端 33.37 s |
| V0b 字面量 `IN (...)` | 44.54 s | 比 `= ANY(数组)` 慢 **11.17 s** |
| `prev` LATERAL 单独成本 | 10.41 s | V0 − V1 |
| `identity_symbol` LATERAL 单独成本 | 13.50 s | V1 − V2 |
| 两个 LATERAL 都去掉后的服务端 | **9.46 s** | 当前查询形态的地板 |

计划中的关键证据：

- 两个 LATERAL 的 `loops=2068420` —— 确认是按行执行 207 万次
- `identity_symbol` 的循环体内还有一个 `Sort (Sort Key: sh.valid_from DESC, sh.id DESC)`，**每行排一次序**，这是它比 `prev` 更贵的原因
- `Parallel Seq Scan on eod_bars rows=7,283,929 loops=3`，`Buffers: shared read=785,968`（约 6.1 GB）—— 整表顺序扫描
- `Sort Method: external merge Disk: 191,888 kB`（另两个 worker 220,520 / 190,056 kB），`temp read=590,779 written=590,945` —— 排序落盘约 600 MB，Gather Merge 在 12.9 s 才完成

### 口径说明（重要）

1. **这是冷盘测量。** `read=1,086,773` 块（约 8.5 GB 真实磁盘读）说明 PG buffer cache 是空的。原始那次 188.31 s / 347 万行折算下来每行更快，多半 PG 缓存是热的。**下文的绝对秒数不要直接套用，比例关系才是稳的。**
2. **行数口径不同**：本次测的是 requested 窗口的 2.07M，原始运行是 coverage 窗口的 3.47M。差异原因见 C-1。

---

## 2. 对上一版建议的修订

上一版（未经测量的估算）与实测的偏差，记录在此以免后续复用错误的判断：

| 项 | 上一版估算 | 实测 | 偏差 |
|---|---:|---:|---|
| `prev` LATERAL → `LAG` | −45 ~ −60 s | −10.41 s | **高估约 5 倍** |
| `identity_symbol` LATERAL | −15 ~ −25 s | −13.50 s | 基本准确 |
| 删 `COUNT(*)` 预飞 | −10 ~ −20 s | −4.59 s | 高估约 3 倍 |
| COPY BINARY 读取 | −30 ~ −50 s，排第 4 位 | 目标是 82.82 s，**应排第 1 位** | 优先级错误 |

另有两项上一版完全没有识别：`eod_bars` 全表扫描、排序落盘 600 MB。

---

## 3. 改动清单

### 批次 A —— 客户端与传输（收益最大）

#### A-1 用 COPY BINARY 读取，绕开 SQLAlchemy 行对象

- **位置**：`backend/src/services/market_data_loader.py:56-70`
- **现状**：`stream_results=True` + `.mappings()` + `fetchmany(5000)`，每行 `dict(raw_row)` 再交给 `_feature_snapshot_from_row` 构造第三个 dict。207 万行 × 3 次字典分配。
- **改法**：改用 psycopg 的 `cursor.copy("COPY (SELECT ...) TO STDOUT (FORMAT BINARY)")`，按二进制块直接解码进 numpy，不生成任何 Python 行对象。
- **依据**：传输 + 驱动 82.82 s，占总读取的 71%。而测量探针只是空循环，生产路径比它更重。
- **先例**：项目里已经在用同一套技术做写入 —— `native_result_repository.py:286`、`support_resistance_persistence_service.py:951`；`native_runtime_service.py` 启动时还会校验 COPY 驱动可用。
- **预计**：−55 ~ −65 s
- **风险**：中。COPY BINARY 的类型解码要逐列对齐；`NULL` 与 `NaN` 的映射要和 `encode` 现有语义一致。
- **验证**：`test_native_nine_strategy_golden.py` 应字节级一致。

**降级方案**（不想动 COPY 时）：改用 `exec_driver_sql` 拿 psycopg 原生游标 + tuple row factory，按位置索引（已有 `PREPARED_INTEGER_INDEX` 这类列序常量），并把 `market_data_loader.py:26` 的 `fetch_size` 从 5,000 提到 50,000。光去掉 `dict(raw_row)` 就有可观收益。

#### A-2 encode 向量化

- **位置**：`backend/src/services/prepared_dataset_service.py:141`
- **现状**：`self.floats[index, column] = float(value) ...` 逐行逐列写。207 万行 × 43 浮点列 ≈ **8,900 万次 numpy 标量赋值**（原始 347 万行口径下约 1.5 亿次），每次都是一趟 Python 层 `__setitem__` 加装箱。
- **改法**：攒 10,000 行组成一个 `(10000, 43)` 的普通 float64 数组，`self.floats[i:i+10000, :] = block` 一次切片赋值。
- **预计**：22.44 s → 3 ~ 5 s
- **风险**：低。纯 Python 改动，不碰 SQL 也不碰语义。

#### A-3 去掉 `floats[:] = np.nan` 的全量预填

- **位置**：`backend/src/services/prepared_dataset_service.py:290`
- **现状**：在 (行数 × 43) 的 float64 memmap 上先写满 NaN（原始口径约 1.2 GB），随后几乎全被覆盖。
- **改法**：改为只填实际未写到的尾部区域；或与 A-2 合并，让批量赋值自带 NaN 填充。
- **预计**：这笔开销多半藏在原始报告「其他未细分开销 21.24 s」里，保守计 −5 s
- **风险**：低。需确认没有代码依赖「未写入行必为 NaN」这一前提。

---

### 批次 B —— SQL 与规划器（改动小、见效快）

#### B-1 给 `eod_bars` 加日期与 instrument 谓词

- **位置**：`backend/src/services/backtest_engine.py:78-119`（`FEATURE_RANGE_SQL` 的 JOIN 子句）
- **现状**：日期条件只加在 `curr.dt_ny` 上，`bars` 上没有，规划器选了整表扫描 + hash join。计划里是 `Parallel Seq Scan on eod_bars rows=7,283,929 loops=3`，读了约 6.1 GB —— 表里还有美股数据，等于为捞 207 万行 A 股读了整张多年期 bar 表。
- **改法**：加两个逻辑冗余但能改变计划的谓词：

  ```sql
  JOIN eod_bars bars
    ON bars.instrument_id = curr.instrument_id
   AND bars.dt_ny = curr.dt_ny
   AND bars.dt_ny BETWEEN :start_date AND :end_date
   AND bars.instrument_id = ANY(:instrument_ids)
  ```

- **预计**：−8 ~ −12 s，I/O 降幅更明显
- **风险**：低。谓词逻辑冗余，结果集不变。
- **验证**：改完重跑测量脚本，确认 `eod_bars` 节点不再是 Seq Scan。

#### B-2 调大 `work_mem`，消除排序落盘

- **位置**：`backend/src/services/market_data_loader.py:50-52`（已有 `SET TRANSACTION READ ONLY` 的位置）
- **现状**：`ORDER BY curr.dt_ny, curr.instrument_id` 与 `daily_features_pkey` 的 `(instrument_id, dt_ny)` 物理序相反，必须重排；`work_mem` 不足导致三个 worker 合计落盘约 600 MB。
- **改法**：同一处加 `SET LOCAL work_mem = '512MB'`。作用域限于该事务，不影响其他连接。
- **预计**：−6 ~ −10 s
- **风险**：低。注意这是**每个排序节点**的上限，并行 worker 各占一份，需按机器内存核定数值。
- **长期正解**：见 C-2。

#### B-3 `= ANY(:ids)` 替代 expanding IN 列表

- **位置**：`backend/src/services/backtest_engine.py:121-127`、`:643` 附近的 `bindparam("instrument_ids", expanding=True)`
- **现状**：SQLAlchemy 的 `expanding=True` 会展开成 5,555 个绑定参数的 IN 列表，主查询和 `COUNT(*)` 预飞各展开一次。
- **改法**：改为传一个整型数组参数。
- **依据**：V0b（字面量 IN 列表）比 V0（`= ANY` 数组）**慢 11.17 s**。
- **预计**：−11 s
- **风险**：低，但**有保留**：V0b 用的是字面量内联，生产用的是参数化 expanding，两者解析路径不完全相同。**落地前需单独 A/B 一次**，不要直接采信这个数。

#### B-4 `identity_symbol` LATERAL → 预加载符号区间

- **位置**：`backend/src/services/backtest_engine.py:97-106`
- **现状**：按行到 `symbol_history` 做索引下降，且循环体内每行还排一次序。A 股代码基本不变，5,555 只股票的符号区间总共只有几千行。
- **改法**：一次性把 `(instrument_id, symbol, valid_from, valid_to)` 读进 Python 侧字典，查询里只带 `instrument_id`，符号在 encoder 里贴回去。写入侧的 `identity_intervals` 已经在做类似的事。
- **预计**：−13.5 s
- **风险**：中。要保证「按时点解析主符号」的语义不变 —— 这是 AGENTS.md 明确要求的 `preserve instrument identity across symbol changes`。
- **验证**：golden 测试 + 抽查若干有过代码变更的标的。

#### B-5 `prev` LATERAL → `LAG` 窗口函数

- **位置**：`backend/src/services/backtest_engine.py:107-112`，消费方在 `feature_snapshot_sql.py:28-36`
- **现状**：整个 LATERAL 只为产出 9 个列（`prev_sma_10/20/50/100/200`、`prev_ema_12/15/20/50`），全都是 `curr` 中已有列的前一日值，却付出 207 万次主键索引下降 + 宽表 `SELECT *`。
- **改法**：

  ```sql
  LAG(curr.sma_10) OVER w AS prev_sma_10,
  ...
  WINDOW w AS (PARTITION BY curr.instrument_id ORDER BY curr.dt_ny)
  ```

- **预计**：−10.4 s
- **风险**：**中，有语义陷阱**。原 LATERAL 的 `dt_ny < curr.dt_ny` 没有下界，窗口第一天会取到 `start_date` 之前的那个交易日；`LAG` 只在过滤后的集合内取，第一天会变成 NULL。**必须把扫描窗口往前放宽若干交易日再丢弃缓冲行** —— manifest 里 `coverage_date_range` 与 `requested_date_range` 分离的机制已经现成。
- **验证**：golden 测试必须过；另需单独断言窗口首日的 `prev_*` 非 NULL。

#### B-6 删掉 `COUNT(*)` 预飞

- **位置**：`backend/src/services/backtest_engine.py:648-670`
- **现状**：真正取数前先跑一遍同样的 `daily_features ⋈ eod_bars`，只为知道数组该开多大。
- **改法**：按 `交易日数 × instrument 数` 开上界，写完按实际 `index` 截断。
- **预计**：−4.6 s
- **风险**：低。注意 `writer()` 里现有的「超出预飞计数就 raise」保护要相应调整。

---

### 批次 C —— 结构性（收益最大但改动也最大）

#### C-1 热身窗口按策略收窄

- **位置**：`backend/src/services/backtest_engine.py:617`

  ```python
  coverage_date_range=(start_date - timedelta(days=400), end_date)
  ```

- **现状**：固定 400 个日历日热身，不分策略类型。算术对得上：回测窗口 577 天 + 400 天 = 977 天，`977 / 577 = 1.69`，`2.07M × 1.69 = 3.50M` ≈ 原始报告的 347 万行。**即每次冷建有四成的 I/O、传输、解码、编码花在回测窗口之外。**
- **改法**：由策略 descriptor 声明自己需要的最长 lookback（C++ 侧已有 descriptor 机制），按策略取值而非全局常量。注意 `daily_features` 里 `sma_200` 这类长周期指标是**预计算列**，内核不需要从原始 bar 重算，所以 400 天大概率是按最坏情况拍的。
- **预计**：若双底策略实际只需 150 天，行数从 3.47M 降到约 2.6M，**所有环节同比省约 25%** —— 这是乘在其他所有优化之上的。
- **风险**：**高**。热身不足会静默改变形态识别与指标初值，属于正确性问题。必须逐策略确认最长 lookback，并用 golden 测试对比收窄前后的信号集是否完全一致。
- **建议**：先做成可配置项、默认值保持 400，再逐策略下调并各自验证。

#### C-2 改成 instrument-major 取数，彻底消除排序

- **现状**：`ORDER BY dt_ny, instrument_id` 与主键物理序相反，2 百万宽行必须重排（见 B-2）。
- **改法**：改按 `(instrument_id, dt_ny)` 取数（与 `daily_features_pkey` 同序，零排序），由 encoder 用 `date_offsets` 表还原按日分组 —— 那张表 `writer()` 里已经在建。
- **预计**：把 B-2 的 12.9 s 排序降到接近 0，并顺带解锁 C-3。
- **风险**：中高。`MarketDataLoader.iter_days()` 的按日 yield 契约要重写，下游 `_execute_paper_orders` 之外的消费者需逐个确认。

#### C-3 按 instrument 分片并行读

- **前置**：C-2（instrument-major 之后分片天然无重叠）
- **改法**：5,555 只切成 N 块，N 条连接各自写入数组的独立切片。
- **预计**：剩余 SQL 与传输时间再除以约 3（4 路）。
- **风险**：中。注意连接池上限与 `work_mem × worker 数` 的内存占用。

#### C-4 缓存分片（治本）

- **位置**：`backend/src/services/prepared_dataset_service.py` 的 `prepared_dataset_key`
- **现状**：key 是整个 manifest 的 sha256，manifest 里含**完整的 5,555 个 instrument_id 列表**和精确日期区间。universe 增删一只股票、或结束日期挪一天，缓存全部作废，重新付一次冷建。`data/backtest_prepared` 现有 3.8 GB，其中相当一部分可能是「只差一点点」的近似副本。
- **改法**：按 `(instrument 分块, 年)` 分片存储，一次回测拼装它需要的那些片。改一只股票只重建一片，挪一天只重建一个年片。
- **预计**：不缩短单次冷建，但让绝大多数运行从「全冷」变成「部分命中」—— **这决定了以后还会不会看到 188 秒这个数字**。
- **风险**：高，是这份计划里最大的一项。建议在批次 A、B 落地并稳定之后单独立项。

---

## 4. 明确不做

- **不增加计算线程 / `BACKTEST_INTRA_RUN_THREADS`。** 策略预热 0.53 s + 原生执行 1.78 s，合计 2.3 s，占总耗时 1.2%。内核不是瓶颈，任何阶段都不应该往这里加资源。
- **不改 C++ 内核。** 本计划涉及的全部改动都在 Python 取数层和 SQL 层。

---

## 5. 验证方法

每一项落地后：

1. **语义验证**：`.venv/bin/python -m unittest backend.tests.test_native_nine_strategy_golden 2>&1 | tail -20`。批次 A 的改动应当**字节级一致**；批次 B 中的 B-4、B-5 和批次 C 的 C-1 会触碰语义边界，golden 必须逐条确认。
2. **性能验证**：重跑 `make explain-feature-query`，与 `tmp/explain_feature_query.txt` 的基线对比。B-1 之后应确认 `eod_bars` 节点不再是 Seq Scan；B-2 之后应确认 `Sort Method` 从 `external merge` 变回 `quicksort`。
3. **端到端**：跑一次同参数的冷建回测，对比 `sql_execute_ms` / `sql_fetch_ms` / `row_decode_ms` / `day_grouping_ms` / `build_dataset_ms` 这几个已有计时器。
4. **每批次结束跑一次全量后端测试**，不要攒到最后。

---

## 6. 预期与顺序

| 批次 | 内容 | 预计落点 |
|---|---|---|
| A | A-1、A-2、A-3 | 188 s → 约 100 ~ 110 s |
| B | B-1 ~ B-6 | → 约 55 ~ 70 s |
| C-1 | 热身窗口收窄 | → 约 45 ~ 55 s |
| C-2 ~ C-4 | 结构性 | 单次进 30 s 内；多数运行变成部分命中 |

建议起手：**A-2 + A-3 + B-1 + B-2 + B-6**。这五项加起来改动量小（B 组三项合计不到 20 行）、风险低、不触碰语义，预计能砍掉 55 ~ 60 秒，同时把基线整理干净，便于后续测准 A-1 的真实收益。

---

## 7. 未决问题

1. **B-3 的 11.17 s 是否成立？** 测的是字面量内联，生产是参数化 expanding。落地前需单独 A/B。
2. **各策略实际需要多长热身？** C-1 的全部收益依赖这个答案，目前无人知道 400 是怎么来的。
3. **原始运行 121.19 s / 347 万行 vs 本次 116.19 s / 207 万行的差异，是否全部来自 PG 缓存冷热？** 建议在 PG 缓存热的状态下重跑一次测量脚本，确认比例关系稳定。
4. **`data/backtest_prepared` 的 3.8 GB 里有多少是近似重复副本？** 这个数会决定 C-4 的优先级。

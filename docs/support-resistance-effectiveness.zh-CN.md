# 支撑/压力区策略有效性研究

[English](support-resistance-effectiveness.md) | [文档索引](README.zh-CN.md)

该流程用于独立验证预注册的 `pivot-slope-regime-v3`，不会改变 allocation、激活 portfolio、启动 scheduler 或提交订单。它与普通自适应研究分离，持久化为父级 `support_resistance_effectiveness_v3` 实验，并包含发现、年度折和封存最终留出子实验。旧 v1/v2 结果不构成 v3 证据，不得继承。

## 协议与动态股票池

行情从 2016-03-18 预热，研究从 2017-03-20 开始；2024-01-02 至 2026-08-27 在候选冻结前保持封存。协议固定随机种子 `20260828`、10,000 次“月份 + instrument”双维分块 bootstrap、40 个交易日事件去重窗，以及最多 200 次回测。

`universePolicy.type=point_in_time_liquid` 与 `basketId`、`symbols` 三选一。T 日收盘确定成员，在下一有效交易日（T+1）开盘执行：

- XNAS、XNYS 或 XASE 的普通股；
- T 日未复权收盘价至少 5 美元；
- 20 日成交额至少 1,000 万美元；
- 至少有 200 个已观测交易日。

标的身份使用 `instrument_id` 和历史 symbol 区间；当前 `is_active` 不会删除历史标的。失去资格只阻止新 BUY，已有仓位继续执行冻结的退出规则。退市后没有可建模价格或现金对价时，主结果按零回收，并把最后有效收盘价回收记录为上界敏感性。

实际研究前必须运行只读股票池预检：

```bash
.venv/bin/python backend/utils/dry_run_point_in_time_universe.py
```

JSON 输出包含逐年合格成员和排除观测数。随后运行 `make check-data CHECK_DATA_ARGS="--strict --json"`，关键错误必须为零，才能启动研究；维护门禁会在整个实验期间排除行情更新。

## 固定候选与时间预算

发现阶段分别运行可交易的 `support_bounce` 和 `breakout_retest`；`resistance_breakout` 只审计，不分配 trial 预算。每种模式固定 4 组 v3 检测器参数（最少拟合 Pivot/跨度、内点容差、斜率上限、区域半宽和时间衰减）与 3 档触发器，共 12 个候选、48 个 trial；两种模式合计 96 个 trial。四状态分类不增加可调参数。模式冠军首先要求 2020 年 base/stress 超额收益均为正，再按超额收益、Sharpe、回撤、集中度和参数哈希确定性排序。

默认策略和两个模式冠军进入 2021、2022、2023 年度折。校准冠军必须在三个年度折中 base 超额收益全部为正，再依次按年度超额收益中位数、最差回撤、stress 衰减和哈希冻结。最终子实验只生成样本外 trial，并增加与 base 成本完全相同的 `base_cache_replay`，用于逐项比较事件、信号、交易、持仓和 NAV。实际排队最多 138 个 trial，空余预算不会重新分配。

最终对外判定归一为 `validated`、`not_validated` 或 `inconclusive`；内部仍记录通过的默认或校准候选。候选必须同时通过预注册的收益、回撤、事件 alpha、总样本量、年度样本量、P&L 集中度、年度折和缓存等价门槛。最终留出失败后不得重定义分层或参数。

除原有指标外，v3 报告必须包含四状态覆盖天数、持续时间和转换次数，状态时间线零重叠/零缺口检查，各 regime/setup 的候选、准入、拒绝、成交和收益，确认下行退出及其后续表现/回撤影响，以及区域与状态缓存重放一致性。任何状态时间线完整性错误都会使 materialization 失败，不能进入研究结果。


当前检测器 revision 为 12。年度折共 36 个 trial（3 候选 × 3 年 × 4 样本/成本组合），最终最多 6 个 trial。应创建新研究，不继续旧三模式协议。只读漏斗、拒单后续收益审查及可选大盘过滤见[策略规则](support-resistance-strategy.zh-CN.md)。

## 报告

UI 和所有文档都读取同一个规范化父级 `report` 对象。稳定运行产物为：

```text
output/research/<study-id>/report.json
output/research/<study-id>/report.zh-CN.md
output/research/<study-id>/report.en-US.md
output/pdf/support-resistance-validation-<study-id>-zh-CN.pdf
output/pdf/support-resistance-validation-<study-id>-en-US.pdf
```

PDF 使用 ReportLab，并嵌入固定 `scifont` 运行依赖随包提供的 Noto Sans SC TrueType 字体；`REPORT_FONT_PATH` 仅作为显式的合规字体覆盖。生成后由 `pypdf` 重开并校验页数与元数据，`pdfplumber` 抽取可见标题和最终判定并与 `report.json` 对照。发布验收还必须使用 Poppler 渲染全部页面，逐页检查字符、表格、图例、页眉页脚、页码、裁切和重叠。文档失败会记录 `report_generation_failed`，保留 JSON 和研究结论，并可通过 `POST /api/agent/research/experiments/{experimentId}/report/retry` 幂等重试。

只读 UI/API 提供父级进度、门槛、子实验和下载：

- `GET /api/research/experiments/{experimentId}/children`
- `GET /api/research/experiments/{experimentId}/report`
- `GET /api/research/experiments/{experimentId}/report-artifacts/{artifactKind}`

报告属于运行产物，不进入维护文档索引；它只是研究证据，不是盈利承诺或实盘安全证明。

## 数据库上线

仓库没有 Alembic。`backend/utils/create_zzzzz_research_experiments.sql` 只包含加法变更：可空 `parent_experiment_id`、非空 `study_kind` 及相关索引。交付代码不会自动应用 DDL。

另行授权应用前，必须确认精确数据库，执行只读 schema/ORM 对照，创建可恢复备份，排空 research worker，并保持 scheduler 和订单提交关闭。v3 还需事务应用 `backend/utils/migrate_pivot_slope_regime_v3.sql` 的新增状态表/索引。使用 `ON_ERROR_STOP` 应用后，校验字段、约束、索引、状态完整性和父子查询，再以并发 1 部署 worker。回滚时先回滚应用；新增字段和状态表可保留用于审计。

```bash
.venv/bin/python backend/utils/preflight_support_resistance_effectiveness_rollout.py
```

## 验证

```bash
PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_support_resistance_effectiveness -v
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
.venv/bin/python -m compileall -q backend/src backend/utils backend/tests
cd frontend && npm run lint && npm run build
```

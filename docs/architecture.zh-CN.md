# 系统架构

[English](architecture.md) | [文档索引](README.zh-CN.md)

## 系统边界

系统由 FastAPI 后端、Next.js Pages Router 前端和 PostgreSQL 持久化组成。Massive 提供市场数据，Alpaca 只用于 paper trading 账户和 paper 订单。可选的 AgentOps 集成负责组织策略草案、有界研究实验以及只交付 Draft PR 的策略代码开发。

```text
Massive -> 日线行情 -> 复权价格 -> daily features
                                 -> 策略信号
                                 -> 回测运行与报告
                                 -> paper 组合 -> Alpaca paper 订单

AgentOps -> 认证后的 /api/agent 工具 -> 策略草案 / 研究实验
        <- 只读研究结果和有界执行证据
```

后端负责领域行为和持久化，FastAPI route 保持精简。前端 HTTP 访问集中在 `frontend/src/api/`，共享请求和响应结构位于 `frontend/src/types/`。

## 市场数据与特征流

1. Instrument 独立于展示 symbol 标识证券身份。
2. `eod_bars` 保存未复权的日线市场观测。
3. 公司行动进入复权价格计算。
4. `adjusted_prices` 保存明确选择的复权 OHLC。
5. `daily_features` 保存策略规则使用的特征。

每日 catch-up 依次检查缺失 EOD、同步公司行动、刷新复权价格并刷新特征。scheduler 的完整性门禁要求目标日期的每条 EOD 都有对应 daily feature，之后才允许执行组合。

复权与未复权价格不能随意互换。任何价格来源变更都必须追踪对信号、成交、持仓、P&L 和基准曲线的影响。

## 策略与回测时序

可复用的策略定义和参数归一化位于 strategy registry。信号生成属于 strategy engine，使回测和 paper trading 共用相同规则。

日线时序模型为：

```text
T 日数据收盘 -> 生成 T 日信号 -> 下一个可交易日开盘 -> 以 T+1 开盘价成交
```

信号不能使用其时间戳之后才可获得的数据。run、日期、symbol、信号和成交必须保持确定性顺序。成本、滑点、佣金、缺失交易日、公司行动、非活跃证券以及部分或碎股成交都属于执行正确性问题。

回测持久化 `StrategyRun`、`Signal`、`Transaction` 和 `PortfolioSnapshot` 证据。研究实验的 trial 创建普通回测 run，而不是维护第二套执行引擎。

`support_resistance` handler 增加了与 paper signal 共用的因果状态检测器：T 日只评估 T-1 冻结的已确认 Pivot/ATR 区域。共享稀疏 materialization 只保存区域成员、角色或状态变化，运行关联和事件单独保存，因此删除某次运行不会删除其他运行引用的缓存证据。源数据版本指纹会阻止复权价格或日特征修正后静默复用陈旧区域。

## Paper Trading 与 Scheduler

一个 `PaperTradingAccount` 可以拥有多个 `StrategyPortfolio`。每个 portfolio 通过一个或多个 `StrategyAllocation` 关联策略和资金设置。

scheduler 的执行步骤是：

1. 使用 `America/New_York` 推导美国交易日期。
2. 等待 daily-feature 完整性门禁。
3. 等待 `PAPER_TRADING_SCHEDULER_RUN_TIME_NY`。
4. 选择 active portfolio 和 `auto_run_enabled=true` 的 allocation。
5. 保持 `portfolio + trade_date + trigger` 以及券商 client-order ID 的幂等性。

Alpaca paper 订单仍会修改远端订单、持仓和 buying power。除非明确需要该外部操作，本地开发和 Agent 联调必须把 scheduler 启用和订单提交都设为 `false`。

## 研究与 AgentOps 集成

Quant 是策略、行情、trial 和回测结果的事实来源。AgentOps 负责工作流定义、审批、结构化 agent、token 计量、外部工具持久化和交付证据。

认证后的 `/api/agent/*` 接口被刻意限制：只校验或创建策略草案和研究实验，接收取消与 token 用量更新，不开放券商订单或组合激活。公开的 `/api/research/*` 接口供 Quant UI 和结果查询使用。

引擎大类研究持久化 `ResearchExperiment -> ExperimentRound -> ExperimentCandidate -> ExperimentTrial -> StrategyRun`。候选参数先归一化并在全实验内去重，再确定性展开；Pareto 排序使用锁定的目标方向和参数哈希并列规则。轮次之间 Quant 进入 `waiting_agent`，AgentOps 重启后只基于有界聚合证据继续规划。universe 和行情输入固化指纹，漂移显式进入 `data_changed`；旧有限网格数据继续只读。详见[研究实验](research-experiments.zh-CN.md)。

预注册支撑/压力区有效性研究在该链路之上增加自引用父实验。父级保存协议哈希、不可变数据指纹、子阶段 ID、冻结冠军、留出门禁、最终判定和产物清单；子实验继续复用 PostgreSQL trial 队列。只读接口开放子实验和生成文档，报告重试仍要求 service 认证，且不授予 portfolio 或券商权限。详见[支撑/压力区策略有效性研究](support-resistance-effectiveness.zh-CN.md)。

## 契约与 Schema 变更

API 变更必须追踪完整链路：

```text
database/model -> service -> FastAPI schema/route -> apps/openapi.yaml
-> frontend type -> frontend API client -> page/component
```

本仓库没有 Alembic 工作流。schema 变更必须同步 ORM model 和建表 SQL，并在 apply 前记录目标数据库、兼容性、rollout、备份和恢复风险。

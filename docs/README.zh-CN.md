# Quant Trading System 文档

[English](README.md)

本索引只包含面向开发者和本地运维者的长期维护文档。带日期的交付报告与验证记录属于历史证据，因此不在这里导航。

## 从这里开始

- [项目 README](../README.zh-CN.md)：功能、安装、命令以及主要 UI 和 API 入口。
- [系统架构](architecture.zh-CN.md)：子系统边界、数据流、执行时序和安全不变量。
- [研究实验](research-experiments.zh-CN.md)：实验输入、trial 生命周期、停止策略、报告和恢复行为。
- [回测性能与 worker 运维](backtest-performance.zh-CN.md)：共享原生内核边界、typed COPY 持久化、durable job、benchmark 门槛和恢复。
- [Tushare A 股数据](tushare-a-share-data.zh-CN.md)：沪深北证券主数据、日线、复权、特征、回测股票池和安全导入流程。
- [BUY 信号强度](signal-strength.zh-CN.md)：各大类 0–100 公式、阈值排名、T 日收盘/下一有效交易日开盘时序和审计字段。
- [底部反转策略](bottom-reversal-strategies.zh-CN.md)：五类形态、三阶段累计目标、审计字段与安全边界。
- [支撑线与压力线策略](support-resistance-strategy.zh-CN.md)：原生因果 Pivot/ATR 区域与状态、入场退出、typed 稀疏持久化、缓存失效和数据库上线。
- [支撑/压力区策略有效性研究](support-resistance-effectiveness.zh-CN.md)：预注册动态股票池验证、父子编排、判定门槛和双语报告交付。
- [Quant 与 AgentOps 本地联调](agent-research-integration.zh-CN.md)：安全联合启动、新算法原生 Draft PR 契约、服务认证、schema 准备和故障处理。

## 事实来源

- 运行行为：`backend/src/`、`frontend/src/` 和 `backend/tests/` 下的聚焦测试。
- 公共 API 契约：`apps/openapi.yaml`、后端 schema/route 和前端 API 类型。
- 本地命令：`Makefile`。
- Agent 贡献规则：`AGENTS.md`。

如果指南与实现或测试不一致，应以实现和测试为准，并在同一变更中修正文档。

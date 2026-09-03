# Quant Trading System Documentation

[中文](README.zh-CN.md)

This index contains the maintained documentation for developers and local operators. Dated delivery reports and validation records are historical evidence and are intentionally excluded.

## Start Here

- [Project README](../README.md): capabilities, setup, commands, and the main UI and API surfaces.
- [System architecture](architecture.md): subsystem boundaries, data flow, execution timing, and safety invariants.
- [Research experiments](research-experiments.md): experiment inputs, trial lifecycle, stop policies, reports, and recovery.
- [Backtest performance and worker operations](backtest-performance.md): shared native-kernel boundaries, typed COPY persistence, durable jobs, benchmark gates, and recovery.
- [BUY signal strength](signal-strength.md): category-specific 0–100 formulas, threshold ranking, day-T-close/next-valid-session-open timing, and audit fields.
- [Bottom-reversal strategies](bottom-reversal-strategies.md): five pattern categories, cumulative staged targets, audit fields, and safety boundaries.
- [Support and resistance strategy](support-resistance-strategy.md): native causal Pivot/ATR zones and regimes, entry/exit rules, typed sparse persistence, cache invalidation, and database rollout.
- [Support/resistance effectiveness study](support-resistance-effectiveness.md): pre-registered dynamic-universe validation, parent/child orchestration, acceptance gates, and bilingual report delivery.
- [Quant and AgentOps local integration](agent-research-integration.md): safe joint startup, native new-algorithm Draft PR contract, service authentication, schema setup, and failure handling.

## Sources of Truth

- Runtime behavior: `backend/src/`, `frontend/src/`, and focused tests under `backend/tests/`.
- Public API contract: `apps/openapi.yaml`, backend schemas and routes, and frontend API types.
- Local commands: `Makefile`.
- Agent contribution rules: `AGENTS.md`.

If a guide disagrees with implementation or tests, treat implementation and tests as authoritative and update the guide in the same change.

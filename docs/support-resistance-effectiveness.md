# Support/Resistance Effectiveness Study

[中文](support-resistance-effectiveness.zh-CN.md) | [Documentation index](README.md)

This workflow independently validates the pre-registered `pivot-slope-regime-v3` strategy without changing allocations, activating a portfolio, starting the scheduler, or submitting orders. It is separate from ordinary adaptive research and is persisted as a parent `support_resistance_effectiveness_v3` experiment with discovery, annual-fold, and sealed-final-holdout children. Results from v1 or v2 are not evidence for v3 and must not be carried forward.

## Protocol and universe

The market-data warm-up begins on 2016-03-18. Research starts on 2017-03-20; 2024-01-02 through 2026-08-27 is sealed until candidate selection is frozen. The protocol has seed `20260828`, 10,000 two-dimensional month/instrument block-bootstrap replicates, a 40-session event de-duplication window, and an absolute limit of 200 backtests.

`universePolicy.type=point_in_time_liquid` is mutually exclusive with `basketId` and `symbols`. Entry eligibility is evaluated at the T close for next-valid-session (T+1) open execution:

- common stock on XNAS, XNYS, or XASE;
- unadjusted close at least USD 5;
- 20-session dollar volume at least USD 10 million;
- at least 200 observed sessions.

Instrument identity uses `instrument_id` and historical symbol intervals; current `is_active` never removes a historical instrument. Losing eligibility blocks only a new BUY. An existing holding follows its frozen exit rules. A delisting without a modeled later price or cash consideration uses zero recovery in the primary result and records last-valid-close recovery as an upper-bound sensitivity.

Run the required read-only universe preflight before an actual study:

```bash
.venv/bin/python backend/utils/dry_run_point_in_time_universe.py
```

The JSON output contains yearly eligible membership and exclusion observations. Run `make check-data CHECK_DATA_ARGS="--strict --json"` and require zero critical failures before starting the study. Market-data updates are excluded for the complete experiment by the maintenance gate.

## Fixed candidate and time budget

Discovery runs the tradable `support_bounce` and `breakout_retest` modes independently; `resistance_breakout` is audit-only and receives no trial budget. Each mode has four frozen v3 detector profiles covering minimum line Pivot count/span, inlier tolerance, slope cap, zone half-width, and recency half-life, plus three frozen trigger profiles: 12 candidates and 48 trials per mode, 96 trials total. The four-regime classifier adds no tunable parameter. A discovery champion requires positive 2020 base and stress excess returns before deterministic ordering by excess return, Sharpe, drawdown, concentration, and parameter hash.

The default plus two mode champions enter the 2021, 2022, and 2023 folds. A calibrated champion must have positive base excess return in all three folds and is frozen by median annual excess return, worst drawdown, stress decay, then hash. The final child runs only out-of-sample trials. It adds `base_cache_replay`, which has the same costs as `base`, so events, signals, transactions, positions, and NAV can be compared exactly. Total scheduled trials are at most 138; unused capacity is never reassigned.

The public final decision is normalized to `validated`, `not_validated`, or `inconclusive`; internal evidence still records whether a default or calibrated candidate passed. Each passing candidate must satisfy all pre-registered return, drawdown, event-alpha, sample-count, annual-count, P&L-concentration, annual-fold, and cache-equivalence gates. A failed final holdout is not used to redefine filters or parameters.

In addition to existing metrics, the v3 report covers days, duration, and transitions for all four regimes; zero-overlap and zero-gap timeline checks; candidate, admitted, rejected, filled, and return results by regime/setup; confirmed-downtrend exits and their post-exit/drawdown impact; and exact replay equality for both zone and regime caches. Any regime timeline integrity error fails the materialization before it can enter a research result.


The current detector revision is 12. Annual folds schedule 36 trials (3 candidates × 3 years × 4 sample/cost combinations), followed by at most 6 final trials. Start a new study; do not continue the old three-mode protocol. For the read-only funnel and rejection-return audit and optional market filtering, see [Strategy rules](support-resistance-strategy.md).

## Reports

All views and documents use the normalized parent `report` object. The stable runtime artifacts are:

```text
output/research/<study-id>/report.json
output/research/<study-id>/report.zh-CN.md
output/research/<study-id>/report.en-US.md
output/pdf/support-resistance-validation-<study-id>-zh-CN.pdf
output/pdf/support-resistance-validation-<study-id>-en-US.pdf
```

The PDF generator uses ReportLab and embeds the Noto Sans SC TrueType font bundled by the pinned `scifont` runtime dependency; `REPORT_FONT_PATH` remains an explicit licensed-font override. `pypdf` reopens each PDF and validates page count and metadata; `pdfplumber` verifies the visible title and decision against `report.json`. Release acceptance additionally renders every page with Poppler and visually checks character rendering, tables, legends, headers, footers, pagination, clipping, and overlap. A document failure records `report_generation_failed`, preserves JSON and the research decision, and can be retried through `POST /api/agent/research/experiments/{experimentId}/report/retry`.

Read-only UI/API surfaces include the parent progress, gates, children, and artifact downloads:

- `GET /api/research/experiments/{experimentId}/children`
- `GET /api/research/experiments/{experimentId}/report`
- `GET /api/research/experiments/{experimentId}/report-artifacts/{artifactKind}`

Reports are runtime artifacts and are excluded from the maintained documentation index. They are research evidence, not a profitability promise or live-trading safety proof.

## Database rollout

The repository has no Alembic workflow. `backend/utils/create_zzzzz_research_experiments.sql` contains additive nullable `parent_experiment_id` and non-null `study_kind` changes plus indexes. Code delivery does not apply this DDL.

Before separately authorized application: resolve the exact database, run a read-only schema/ORM comparison, take a restorable backup, drain research workers, and keep scheduler and order submission disabled. V3 also applies the additive regime table and indexes in `backend/utils/migrate_pivot_slope_regime_v3.sql` in one transaction. Use `ON_ERROR_STOP`, verify columns, constraints, regime integrity, indexes, and parent/child reads, then deploy workers at concurrency one. Roll back application code first; additive columns and the regime table may remain for audit.

```bash
.venv/bin/python backend/utils/preflight_support_resistance_effectiveness_rollout.py
```

## Validation

```bash
PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_support_resistance_effectiveness -v
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
.venv/bin/python -m compileall -q backend/src backend/utils backend/tests
cd frontend && npm run lint && npm run build
```

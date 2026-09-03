from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from starlette.requests import Request
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.api.dashboard import router, get_dashboard_overview
from src.core.db import get_db
from src.models.tables import (Base, Strategy, StrategyRun, BacktestJob, BacktestWorkerManager,
    ExperimentTrial, ResearchExperiment, ExperimentCandidate, ExperimentRound,
    PaperTradingAccount, StrategyPortfolio, StrategyAllocation, MarketDataMaintenanceState)
from src.services.dashboard_service import build_dashboard_overview
from src.services.paper_trading_scheduler import PaperTradingDailyScheduler, PaperTradingSchedulerConfig
from src.services.strategy_registry import build_strategy_catalog

NOW = datetime(2026, 9, 3, 18, tzinfo=UTC)
RESEARCH = dict(enabled=False, state="disabled", active_trials=0, configured_concurrency=2)
SCHEDULER = dict(status="disabled", enabled=False, submit_orders=False)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite+pysqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.defaults = next(i['defaults'] for i in build_strategy_catalog() if i['strategy_type'] == 'trend')
        self.sql = []
        event.listen(self.engine, 'before_cursor_execute', lambda conn, cursor, statement, params, context, many: self.sql.append(statement))

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def strategy(self, **overrides):
        s = Strategy(strategy_key=str(uuid4()), name='Trend', strategy_type='trend', params=self.defaults,
                     status='active', version=1, created_at=NOW, updated_at=NOW)
        for k, v in overrides.items(): setattr(s, k, v)
        self.db.add(s); self.db.flush()
        return s

    def make_run(self, s, **overrides):
        r = StrategyRun(strategy_id=s.id, strategy_version=s.version, mode='backtest', status='completed',
                        config_snapshot=self.defaults, summary_metrics={'total_return': .2, 'sharpe': 1.5, 'max_drawdown': -.1, 'trade_count': 12},
                        requested_at=NOW - timedelta(hours=1), finished_at=NOW, created_at=NOW, updated_at=NOW)
        for k, v in overrides.items(): setattr(r, k, v)
        self.db.add(r); self.db.flush()
        return r

    def overview(self):
        self.db.commit(); self.sql.clear()
        with patch('src.services.paper_account_service.build_alpaca_client_for_account', side_effect=AssertionError('broker called')):
            result = build_dashboard_overview(self.db, research=RESEARCH, scheduler=SCHEDULER, checked_at=NOW)
        self.assertTrue(all(s.lstrip().upper().startswith('SELECT') for s in self.sql), self.sql)
        return result

    def test_activity_includes_strategy_category_for_backtest_colors(self):
        strategy = self.strategy()
        run = self.make_run(strategy)
        activity = self.overview().activity
        row = next(item for item in activity if item.id == f"run:{run.id}")
        self.assertEqual(row.strategy_type, "trend")

    def test_empty_read_only_api_and_missing_maintenance(self):
        result = self.overview()
        self.assertEqual(result.research_kpis.active_strategies, 0)
        self.assertEqual(result.strategy_evidence, [])
        self.assertEqual(result.paper_summary.account_count, 0)
        self.assertEqual([s.status for s in result.system], ['unknown', 'failed', 'disabled', 'disabled', 'unknown'])
        self.assertIsNone(self.db.get(MarketDataMaintenanceState, 1))
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        response = get_dashboard_overview(Request({'type': 'http', 'app': app}), self.db)
        self.assertEqual(response.system[-1].reason, 'broker_unchecked')

    def test_counts_exceed_list_limit_and_trial_not_double_counted(self):
        s = self.strategy()
        for _ in range(65): self.make_run(s)
        queued = self.make_run(s, status='queued', finished_at=None)
        self.db.add(BacktestJob(run_id=queued.id, status='queued', source='research'))
        e = ResearchExperiment(workflow_run_id='wf', idempotency_key='experiment', status='running')
        self.db.add(e); self.db.flush()
        for ordinal, run_id in enumerate([None, queued.id]):
            self.db.add(ExperimentTrial(experiment_id=e.id, ordinal=ordinal, trial_key=str(ordinal), status='queued',
                sample_kind='in_sample', cost_scenario='base', params={}, params_hash=str(ordinal),
                window_start=NOW.date(), window_end=NOW.date(), backtest_run_id=run_id))
        result = self.overview()
        self.assertEqual(result.task_summary.completed_last_24h, 65)
        self.assertEqual(result.task_summary.waiting_research, 1)
        self.assertEqual(result.research_kpis.queued_backtests, 1)
        self.assertEqual(len(result.activity), 20)
        self.assertIn('backtest_blocked', [a.code for a in result.alerts])
        self.assertIn('research_blocked', [a.code for a in result.alerts])

    def test_manual_current_config_evidence_excludes_overrides_and_old_versions(self):
        s = self.strategy()
        manual = self.make_run(s)
        self.make_run(s, strategy_version=0, finished_at=NOW + timedelta(minutes=1))
        candidate = self.make_run(s, finished_at=NOW + timedelta(minutes=2))
        self.db.add(BacktestJob(run_id=candidate.id, source='verification', status='completed'))
        result = self.overview().strategy_evidence[0]
        self.assertEqual(result.backtest_id, str(manual.id))
        self.assertEqual(result.evidence_status, 'available')
        self.assertEqual(result.sharpe, 1.5)
        s.params = {**self.defaults, 'risk': {'max_positions': 3}}
        result = self.overview().strategy_evidence[0]
        self.assertEqual(result.evidence_status, 'configuration_changed')
        self.assertIsNone(result.total_return)

    def test_invalid_json_and_missing_metrics_are_local(self):
        s = self.strategy()
        r = self.make_run(s, summary_metrics={'total_return': 'bad', 'trade_count': -1})
        result = self.overview().strategy_evidence[0]
        self.assertIsNone(result.total_return)
        self.assertIsNone(result.sharpe)
        self.assertEqual(result.issues, ['invalid_metrics'])
        r.config_snapshot = []
        self.assertEqual(self.overview().strategy_evidence[0].evidence_status, 'invalid')

    def test_resolved_universe_is_run_scope_not_a_rule_change(self):
        s = self.strategy()
        self.make_run(s, config_snapshot={**self.defaults, 'universe': {'symbols': ['SPY'], 'selection_mode': 'explicit'}})
        self.assertEqual(self.overview().strategy_evidence[0].evidence_status, 'available')

    def test_research_lineage_and_counts_are_separate_from_current_backtest(self):
        s = self.strategy()
        e = ResearchExperiment(workflow_run_id='wf', idempotency_key='lineage', status='completed', finished_at=NOW)
        self.db.add(e); self.db.flush()
        round_row = ExperimentRound(experiment_id=e.id, ordinal=1, status='completed')
        self.db.add(round_row); self.db.flush()
        c = ExperimentCandidate(experiment_id=e.id, round_id=round_row.id, ordinal=1,
            params_hash='candidate', params={}, pareto_rank=1, promoted_strategy_id=s.id,
            aggregate_metrics={'verification': {'status': 'completed'}})
        self.db.add(c)
        result = self.overview()
        self.assertEqual(result.research_progress.verified_candidates, 1)
        self.assertEqual(result.research_progress.promoted_strategies, 1)
        self.assertEqual(result.strategy_evidence[0].verification_status, 'completed')
        self.assertEqual(result.strategy_evidence[0].evidence_status, 'missing')
        c.aggregate_metrics = {'verification': ['broken']}
        result = self.overview()
        self.assertEqual(result.research_progress.verified_candidates, 0)
        self.assertEqual(result.strategy_evidence[0].issues, ['invalid_research_evidence'])

    def test_manager_database_error_is_not_hidden_as_unknown(self):
        from sqlalchemy.exc import OperationalError
        self.db.commit()
        BacktestWorkerManager.__table__.drop(self.engine)
        with self.assertRaises(OperationalError):
            build_dashboard_overview(self.db, research=RESEARCH, scheduler=SCHEDULER, checked_at=NOW)

    def add_portfolio(self, index, s, *, pct='.5'):
        a = PaperTradingAccount(name=f'Account {index}')
        self.db.add(a); self.db.flush()
        p = StrategyPortfolio(name=f'Portfolio {index}', paper_account_id=a.id)
        self.db.add(p); self.db.flush()
        self.db.add(StrategyAllocation(strategy_id=s.id, portfolio_name=p.name, allocation_pct=Decimal(pct)))
        return p

    def test_paper_configuration_alerts_and_scheduler_snapshot(self):
        s = self.strategy()
        p = self.add_portfolio(1, s, pct='.7')
        other = self.strategy(strategy_type='custom', params={})
        self.db.add(StrategyAllocation(strategy_id=other.id, portfolio_name=p.name, allocation_pct=Decimal('.6')))
        r = self.make_run(s, mode='paper', status='failed', config_snapshot={'paper_trading': {'portfolio_name': p.name, 'trigger': 'scheduler', 'submit_orders': False}})
        self.db.add(MarketDataMaintenanceState(id=1, status='failed', updated_at=NOW))
        result = self.overview()
        codes = [a.code for a in result.alerts]
        self.assertIn('overallocated', codes)
        self.assertIn('scheduler_failed', codes)
        self.assertIn('allocation_configuration', codes)
        self.assertNotIn('strategy_configuration', codes)
        self.assertEqual(result.paper_summary.portfolios[0].latest_run_id, str(r.id))
        self.assertFalse(result.paper_summary.portfolios[0].submit_orders)
        self.assertEqual(result.system[0].status, 'failed')
        from datetime import time as clock_time
        scheduler = PaperTradingDailyScheduler(PaperTradingSchedulerConfig(False, clock_time(23, 30), 60, False, True))
        self.assertEqual(scheduler.status_snapshot(), SCHEDULER)

    def test_query_count_stable_with_account_growth_and_bounded_payload(self):
        s = self.strategy()
        self.add_portfolio(0, s)
        started = time.perf_counter(); small = self.overview(); small_ms = (time.perf_counter() - started) * 1000
        small_queries = len(self.sql)
        for i in range(1, 80): self.add_portfolio(i, s)
        started = time.perf_counter(); large = self.overview(); large_ms = (time.perf_counter() - started) * 1000
        self.assertEqual(len(self.sql), small_queries)
        self.assertEqual(large.paper_summary.portfolio_count, 80)
        self.assertEqual(len(large.paper_summary.portfolios), 10)
        print(f'\nDashboard isolated SQLite 1/80 portfolios: queries={small_queries}/{len(self.sql)}, ms={small_ms:.1f}/{large_ms:.1f}, bytes={len(small.model_dump_json())}/{len(large.model_dump_json())}')

    def test_healthy_idle_and_activity_determinism(self):
        self.db.add(BacktestWorkerManager(manager_id='leader', hostname='test', pid=1, status='idle', is_leader=True, heartbeat_at=NOW))
        s = self.strategy(); self.make_run(s); self.make_run(s, status='failed')
        result = self.overview()
        self.assertEqual(result.system[1].status, 'healthy')
        self.assertEqual(result.research_kpis.running_backtests, 0)
        ids = [a.id for a in result.activity]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(ids, [a.id for a in self.overview().activity])


if __name__ == '__main__': unittest.main()

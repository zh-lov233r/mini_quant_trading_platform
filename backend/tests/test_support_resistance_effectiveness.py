from __future__ import annotations

from datetime import date
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from src.schemas.research import PointInTimeUniversePolicy, SupportResistanceValidationProtocol
from src.services.backtest_universe_service import point_in_time_entry_eligible
from src.services.research_experiment_service import _finalize_if_ready
from src.services.support_resistance_validation_report_service import (
    _bootstrap_interval,
    _benjamini_hochberg,
    render_markdown,
    write_report_artifacts,
)
from src.services.support_resistance_validation_service import fixed_mode_candidates


class PointInTimeUniversePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = PointInTimeUniversePolicy().model_dump(mode="json", by_alias=True)
        self.snapshot = {
            "dt_ny": date(2024, 1, 2),
            "asset_type": "CS",
            "exchange": "XNAS",
            "listed_at": date(2020, 1, 1),
            "delisted_at": None,
            "close_unadjusted": 5.0,
            "dollar_volume_20": 10_000_000.0,
            "history_sessions": 200,
        }

    def test_thresholds_are_inclusive_and_causal(self) -> None:
        self.assertEqual(point_in_time_entry_eligible(self.snapshot, self.policy), (True, None))

    def test_each_exclusion_reason_is_deterministic(self) -> None:
        cases = (
            ("asset_type", {"asset_type": "ETF"}),
            ("exchange", {"exchange": "ARCX"}),
            ("before_listing", {"listed_at": date(2025, 1, 1)}),
            ("after_delisting", {"delisted_at": date(2023, 12, 31)}),
            ("price", {"close_unadjusted": 4.99}),
            ("liquidity", {"dollar_volume_20": 9_999_999.0}),
            ("history", {"history_sessions": 199}),
        )
        for reason, replacement in cases:
            with self.subTest(reason=reason):
                eligible, actual = point_in_time_entry_eligible(
                    {**self.snapshot, **replacement}, self.policy
                )
                self.assertFalse(eligible)
                self.assertEqual(actual, reason)


class PreRegisteredCandidateTests(unittest.TestCase):
    def test_one_bounce_discovery_advances_to_three_eight_trial_folds(self) -> None:
        from src.services.support_resistance_validation_service import _create_fold_children
        candidate = SimpleNamespace(id="candidate", params_hash="hash", parameter_overrides={})
        parent = SimpleNamespace(id="parent", spec={}, child_experiments=[SimpleNamespace(
            spec={"validationPhase": "discovery:support_bounce"}, status="completed")],
            run_manifest={}, workflow_run_id="workflow", idempotency_key="key")
        db = MagicMock()
        db.get.return_value = parent
        module = "src.services.support_resistance_validation_service"
        with patch(f"{module}.SupportResistanceEffectivenessSpec.model_validate", return_value=SimpleNamespace(name="test")), \
             patch(f"{module}._mode_champion", return_value=candidate), \
             patch(f"{module}._child_spec", return_value={}) as child_spec, \
             patch("src.services.adaptive_research_service.create_category_study", side_effect=[
                 SimpleNamespace(id=str(i), spec={}) for i in range(3)]):
            _create_fold_children(db, parent)
        self.assertEqual(child_spec.call_count, 3)
        for call in child_spec.call_args_list:
            self.assertEqual(call.kwargs["max_trials"], 8)
            self.assertEqual(len(call.kwargs["proposals"]), 2)
        self.assertEqual(parent.progress, {"phase": "annual_folds", "total": 200, "scheduled": 72, "completed": 48})

    def test_direct_breakout_has_no_discovery_budget(self) -> None:
        with self.assertRaisesRegex(ValueError, "only support_bounce"):
            fixed_mode_candidates("resistance_breakout")

    def test_each_setup_has_exactly_twelve_unique_single_mode_candidates(self) -> None:
        switches = {
            "support_bounce": "signal.support_bounce_enabled",
        }
        for setup, enabled_key in switches.items():
            candidates = fixed_mode_candidates(setup)
            self.assertEqual(len(candidates), 12)
            self.assertEqual(
                len({tuple(sorted(item.overrides.items())) for item in candidates}),
                12,
            )
            for item in candidates:
                self.assertTrue(item.overrides[enabled_key])
                self.assertEqual(
                    sum(
                        bool(item.overrides[key])
                        for key in switches.values()
                    ),
                    1,
                )

    def test_validation_protocol_is_frozen(self) -> None:
        protocol = SupportResistanceValidationProtocol()
        self.assertEqual(protocol.max_backtests, 200)
        self.assertEqual(protocol.bootstrap_seed, 20260828)
        with self.assertRaises(ValidationError):
            SupportResistanceValidationProtocol(maxBacktests=201)


class DeterministicReportTests(unittest.TestCase):
    def test_effectiveness_parent_is_not_finalized_as_empty_generic_study(self) -> None:
        parent = type(
            "EffectivenessParent",
            (),
            {"study_kind": "support_resistance_effectiveness_v3", "status": "queued"},
        )()
        _finalize_if_ready(None, parent)
        self.assertEqual(parent.status, "queued")

    def test_bootstrap_is_deterministic(self) -> None:
        observations = [
            {
                "originMonth": f"2023-{index + 1:02d}",
                "instrumentId": index % 2,
                "horizons": {"20": {"betaAdjustedAlpha": value}},
            }
            for index, value in enumerate((0.01, 0.02, -0.01, 0.04, 0.03, 0.00))
        ]
        first = _bootstrap_interval(observations, horizon=20, seed=20260828, replicates=200)
        second = _bootstrap_interval(observations, horizon=20, seed=20260828, replicates=200)
        self.assertEqual(first, second)

    def test_benjamini_hochberg_is_deterministic_at_q_point_one(self) -> None:
        result = _benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.20}, q=0.10)
        self.assertTrue(result["tests"]["a"]["rejected"])
        self.assertTrue(result["tests"]["b"]["rejected"])
        self.assertFalse(result["tests"]["c"]["rejected"])

    def test_json_markdown_and_pdf_share_decision_and_metrics(self) -> None:
        report = {
            "schemaVersion": 1,
            "studyId": "fixture-study",
            "studyKind": "support_resistance_effectiveness_v3",
            "status": "completed",
            "decision": "not_validated",
            "disclaimer": "Research evidence only; no profitability guarantee.",
            "hypothesis": "Fixture hypothesis",
            "protocol": {"bootstrapSeed": 20260828},
            "protocolHash": "abc123",
            "backtestBudget": {"maximum": 200, "scheduled": 196},
            "children": [{"id": "child-1", "phase": "annual_2021", "status": "completed"}],
            "finalCandidates": [{
                "candidateId": "candidate-1",
                "paramsHash": "params123",
                "rationale": "pre-registered default",
                "passed": False,
                "base": {"excess_return": 0.0123, "max_drawdown": 0.10, "pnl_concentration": 0.25},
                "stress": {"excess_return": -0.0045},
                "eventStudy": {"eventCount": 12, "horizons": {"20": {"mean": 0.01, "lower95": -0.002}}},
                "annualFolds": [],
                "acceptanceGates": {"finalBaseExcessPositive": True, "finalStressExcessPositive": False},
            }],
            "charts": {
                "costDecay": [{"base": 0.0123, "stress": -0.0045}],
                "pnlConcentration": [{"value": 0.25}],
            },
            "limitations": ["Fixture only."],
        }
        zh = render_markdown(report, "zh-CN")
        en = render_markdown(report, "en-US")
        self.assertIn("not_validated", zh)
        self.assertIn("1.23%", zh)
        self.assertIn("not_validated", en)
        self.assertIn("1.23%", en)
        with tempfile.TemporaryDirectory() as directory:
            artifacts = write_report_artifacts(report, Path(directory))
            self.assertEqual(artifacts["status"], "generated_verified")
            self.assertEqual(set(artifacts["files"]), {"json", "markdownZh", "markdownEn", "pdfZh", "pdfEn"})
            self.assertGreater(artifacts["pdf"]["zh"]["verification"]["pageCount"], 0)
            self.assertGreater(artifacts["pdf"]["en"]["verification"]["pageCount"], 0)


if __name__ == "__main__":
    unittest.main()

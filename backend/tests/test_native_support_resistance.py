from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import math
import unittest

import quant_kernel

from src.services.strategy_registry import normalize_strategy_params
from src.services.support_resistance_service import (
    Pivot,
    SupportResistanceSymbolState,
    Zone,
    advance_symbol,
)


def _oracle_stored_zone_price(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP))


def _oracle_weighted_median(values: list[tuple[float, float]]) -> float:
    weighted = sorted(values, key=lambda item: item[0])
    threshold = sum(weight for _, weight in weighted) / 2.0
    cumulative = 0.0
    for value, weight in weighted:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return weighted[-1][0]


def _oracle_fit_pivot_line(
    pivots: list[Pivot],
    current_index: int,
    signal_cfg: dict[str, object],
) -> tuple[list[Pivot], float, float, float, float] | None:
    minimum = int(signal_cfg["min_line_pivots"])
    minimum_span = int(signal_cfg["min_line_span_sessions"])
    if len(pivots) < minimum or pivots[-1].session_index - pivots[0].session_index < minimum_span:
        return None
    half_life = int(signal_cfg["decay_half_life"])
    weights = {
        pivot.pivot_key: 0.5 ** (max(current_index - pivot.session_index, 0) / half_life)
        for pivot in pivots
    }

    def fit(items: list[Pivot]) -> tuple[float, float] | None:
        slopes = [
            (
                (right.price - left.price) / (right.session_index - left.session_index),
                (weights[left.pivot_key] * weights[right.pivot_key]) ** 0.5,
            )
            for left_index, left in enumerate(items)
            for right in items[left_index + 1 :]
            if right.session_index - left.session_index >= minimum_span
        ]
        if not slopes:
            return None
        slope = _oracle_weighted_median(slopes)
        intercept = _oracle_weighted_median(
            [
                (pivot.price - slope * pivot.session_index, weights[pivot.pivot_key])
                for pivot in items
            ]
        )
        return slope, intercept

    initial = fit(pivots)
    if initial is None:
        return None
    initial_slope, initial_intercept = initial
    tolerance = float(signal_cfg["line_inlier_tolerance_atr"])
    inliers = [
        pivot
        for pivot in pivots
        if abs(pivot.price - (initial_intercept + initial_slope * pivot.session_index))
        <= tolerance * pivot.atr
    ]
    if len(inliers) < minimum or inliers[-1].session_index - inliers[0].session_index < minimum_span:
        return None
    refined = fit(inliers)
    if refined is None:
        return None
    slope, intercept = refined
    representative_atr = _oracle_weighted_median(
        [(pivot.atr, weights[pivot.pivot_key]) for pivot in inliers]
    )
    if representative_atr <= 0:
        return None
    if abs(slope) / representative_atr > float(signal_cfg["max_abs_slope_atr_per_session"]):
        return None
    total_weight = sum(weights[pivot.pivot_key] for pivot in inliers)
    residual_atr = sum(
        weights[pivot.pivot_key]
        * abs(pivot.price - (intercept + slope * pivot.session_index))
        / max(pivot.atr, 1e-12)
        for pivot in inliers
    ) / total_weight
    return inliers, intercept + slope * current_index, slope, residual_atr, total_weight


def _oracle_build_entry_channel(zones: list[Zone], close: float, trade_date: date) -> dict:
    def valid(zone: Zone) -> bool:
        values = (zone.center, zone.lower, zone.upper, zone.atr, zone.slope_per_session)
        return (
            all(math.isfinite(value) for value in values)
            and zone.atr > 0
            and 0 < zone.lower <= zone.center <= zone.upper
        )

    support = min(
        (
            zone
            for zone in zones
            if zone.role == "support"
            and zone.status == "active"
            and valid(zone)
            and zone.upper <= close
        ),
        key=lambda zone: (
            close - zone.upper,
            -zone.pivot_count,
            -zone.last_pivot_date.toordinal(),
            zone.fit_residual_atr,
            zone.zone_key,
        ),
        default=None,
    )
    resistance = min(
        (
            zone
            for zone in zones
            if zone.role == "resistance"
            and zone.status == "active"
            and valid(zone)
            and zone.lower >= close
        ),
        key=lambda zone: (
            zone.lower - close,
            -zone.pivot_count,
            -zone.last_pivot_date.toordinal(),
            zone.fit_residual_atr,
            zone.zone_key,
        ),
        default=None,
    )
    payload = {
        "semantics": "support_upper_to_resistance_lower_v1",
        "signal_trade_date": trade_date.isoformat(),
        "signal_close": close,
        "valid": False,
        "reason_code": None,
        "support_zone_key": support.zone_key if support else None,
        "resistance_zone_key": resistance.zone_key if resistance else None,
        "lower": support.upper if support else None,
        "upper": resistance.lower if resistance else None,
        "lower_slope_per_session": support.slope_per_session if support else None,
        "upper_slope_per_session": resistance.slope_per_session if resistance else None,
        "support_zone": support.snapshot() if support else None,
        "resistance_zone": resistance.snapshot() if resistance else None,
    }
    if support is None or resistance is None:
        payload["reason_code"] = "missing_support_or_resistance"
    elif not support.upper < resistance.lower:
        payload["reason_code"] = "unordered_or_overlapping_inner_edges"
    elif not support.upper <= close <= resistance.lower:
        payload["reason_code"] = "signal_close_outside_inner_edges"
    else:
        payload["valid"] = True
        payload["reason_code"] = "valid_inner_edge_channel"
    return payload


def _oracle_project_entry_channel(channel: dict | None, sessions: int = 1) -> dict:
    payload = dict(channel or {})
    if not payload.get("valid"):
        return {
            **payload,
            "valid": False,
            "reason_code": str(payload.get("reason_code") or "missing_valid_entry_channel"),
        }
    try:
        lower = float(payload["lower"]) + float(payload["lower_slope_per_session"]) * sessions
        upper = float(payload["upper"]) + float(payload["upper_slope_per_session"]) * sessions
    except (KeyError, TypeError, ValueError):
        return {**payload, "valid": False, "reason_code": "missing_channel_projection_values"}
    if not all(math.isfinite(value) and value > 0 for value in (lower, upper)):
        return {**payload, "valid": False, "reason_code": "invalid_channel_projection_values"}
    if lower >= upper:
        return {
            **payload,
            "lower": lower,
            "upper": upper,
            "valid": False,
            "reason_code": "projected_inner_edges_crossed",
        }
    return {
        **payload,
        "lower": lower,
        "upper": upper,
        "projected_sessions": sessions,
        "valid": True,
        "reason_code": "valid_projected_inner_edge_channel",
    }


def _oracle_entry_price_is_inside_channel(channel: dict | None, price: float) -> tuple[bool, str]:
    if not channel or not channel.get("valid"):
        return False, str((channel or {}).get("reason_code") or "missing_valid_entry_channel")
    try:
        lower = float(channel["lower"])
        upper = float(channel["upper"])
        resolved_price = float(price)
    except (KeyError, TypeError, ValueError):
        return False, "invalid_entry_channel_values"
    if not all(math.isfinite(value) and value > 0 for value in (lower, upper, resolved_price)):
        return False, "non_finite_entry_channel_values"
    if lower >= upper:
        return False, "unordered_entry_channel"
    if not lower <= resolved_price <= upper:
        return False, "entry_price_outside_valid_channel"
    return True, "entry_price_inside_valid_channel"


class NativeSupportResistanceParityTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        params = normalize_strategy_params("support_resistance", {})
        self.signal = params["signal"]
        self.risk = params["risk"]
        self.native = quant_kernel.support_resistance

    def _assert_value_equal(self, actual: object, expected: object) -> None:
        if isinstance(expected, float):
            self.assertIsInstance(actual, (int, float))
            if math.isnan(expected):
                self.assertTrue(math.isnan(float(actual)))
            else:
                self.assertAlmostEqual(float(actual), expected, delta=1e-10)
            return
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict)
            self.assertEqual(set(actual), set(expected))
            for key in expected:
                with self.subTest(field=key):
                    self._assert_value_equal(actual[key], expected[key])
            return
        if isinstance(expected, (list, tuple)):
            self.assertIsInstance(actual, (list, tuple))
            self.assertEqual(len(actual), len(expected))
            for actual_item, expected_item in zip(actual, expected, strict=True):
                self._assert_value_equal(actual_item, expected_item)
            return
        self.assertEqual(actual, expected)

    def test_detector_identity_and_parameters_match_python(self) -> None:
        keys = (
            "pivot_left_bars",
            "pivot_right_bars",
            "detection_window",
            "min_line_pivots",
            "min_line_span_sessions",
            "max_zones_per_kind",
            "pivot_tolerance_atr",
            "line_inlier_tolerance_atr",
            "max_abs_slope_atr_per_session",
            "zone_half_width_atr",
            "decay_half_life",
        )
        expected = {
            "implementation_revision": 14,
            "regime_logic_revision": 4,
            **{key: self.signal[key] for key in keys},
        }
        actual = self.native.normalized_detector_params({"signal": self.signal})

        self.assertEqual(self.native.DETECTOR_IMPLEMENTATION_REVISION, 14)
        self.assertEqual(self.native.REGIME_LOGIC_REVISION, 4)
        self.assertEqual(
            self.native.ENTRY_CHANNEL_SEMANTICS,
            "support_upper_to_resistance_lower_v1",
        )
        self.assertEqual(actual, expected)

    def test_numeric_24_10_half_up_and_geometry_match_python(self) -> None:
        for value in (
            100.12345678916,
            1.23456789015,
            -1.23456789015,
            0.00000000005,
            -0.00000000005,
            1e-12,
            99999999999999.123456789,
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    self.native.stored_zone_price(value),
                    _oracle_stored_zone_price(value),
                )

        cases = (
            (100.0, 99.0, 101.0, 2.0, 0.1),
            (100.0, 0.0, 101.0, 2.0, 0.1),
            (100.0, 101.0, 99.0, 2.0, 0.1),
            (100.0, 99.0, 101.0, 0.0, 0.1),
            (100.0, 99.0, 101.0, 2.0, math.inf),
        )
        for center, lower, upper, atr, slope in cases:
            with self.subTest(values=(center, lower, upper, atr, slope)):
                self.assertEqual(
                    self.native.valid_zone_values(center, lower, upper, atr, slope),
                    all(math.isfinite(value) for value in (center, lower, upper, atr, slope))
                    and atr > 0
                    and 0 < lower <= center <= upper,
                )

    def test_zone_identity_uses_full_membership_and_revived_date(self) -> None:
        pivots = [
            self._pivot(1, 10.0),
            self._pivot(2, 11.0),
            self._pivot(3, 12.0),
        ]
        raw_pivots = [asdict(pivot) for pivot in reversed(pivots)]

        self.assertEqual(
            self.native.new_zone_key("low", raw_pivots),
            "srz_"
            + sha256(
                f"low|{'|'.join(sorted(pivot.pivot_key for pivot in pivots))}".encode()
            ).hexdigest()[:20],
        )
        shorter = self.native.new_zone_key("low", raw_pivots[:-1])
        self.assertNotEqual(shorter, self.native.new_zone_key("low", raw_pivots))
        zone_key = self.native.new_zone_key("low", raw_pivots)
        self.assertEqual(
            self.native.revived_zone_key(zone_key, date(2025, 2, 3)),
            "srz_"
            + sha256(f"{zone_key}|revived|2025-02-03".encode()).hexdigest()[:20],
        )

    def test_projection_and_invalid_tombstone_match_python_state_machine(self) -> None:
        active = self._zone("active", "support", 100.0)
        active.slope_per_session = 0.25
        expected_projection = active.snapshot()
        expected_projection.update(
            {
                "center": _oracle_stored_zone_price(100.5),
                "lower": _oracle_stored_zone_price(99.5),
                "upper": _oracle_stored_zone_price(101.5),
            }
        )
        self._assert_value_equal(
            self.native.project_zone(active.snapshot(), 2),
            expected_projection,
        )

        falling = self._zone("falling", "support", 0.2)
        falling.lower = 0.1
        falling.upper = 0.3
        falling.anchor_center = 0.2
        falling.anchor_lower = 0.1
        falling.anchor_upper = 0.3
        falling.slope_per_session = -0.2
        state = SupportResistanceSymbolState(
            history=[self._bar(0, close=0.8)],
            zones={falling.zone_key: falling},
        )
        advance_symbol(
            state,
            self._bar(1, close=0.7),
            self.signal,
            self.risk,
            emit_signals=False,
        )
        expected_versions = [
            item for item in state.zone_versions if item["status"] == "expired"
        ]

        self.assertEqual(len(expected_versions), 1)
        self.assertEqual(expected_versions[0]["slope_per_session"], -0.2)
        self.assertEqual(expected_versions[0]["end_reason"], "invalid_geometry")
        self.assertEqual(state.phase_start, date(2025, 1, 2))

    def test_weighted_theil_sen_fit_matches_python(self) -> None:
        pivots = [
            self._pivot(0, 100.0),
            self._pivot(10, 101.0),
            self._pivot(20, 102.0),
            self._pivot(15, 110.0),
        ]
        expected = _oracle_fit_pivot_line(pivots, 20, self.signal)
        self.assertIsNotNone(expected)
        assert expected is not None
        inliers, center, slope, residual_atr, total_weight = expected

        actual = self.native.fit_pivot_line(
            [asdict(pivot) for pivot in pivots],
            20,
            self.signal,
        )

        self.assertIsNotNone(actual)
        self._assert_value_equal(
            actual,
            {
                "inlier_pivot_keys": [pivot.pivot_key for pivot in inliers],
                "center": center,
                "slope": slope,
                "residual_atr": residual_atr,
                "total_weight": total_weight,
            },
        )
        two_pivot_fit = self.native.fit_pivot_line(
            [asdict(self._pivot(0, 100.0)), asdict(self._pivot(10, 101.0))],
            20,
            self.signal,
        )
        self.assertIsNotNone(two_pivot_fit)
        three_pivot_signal = {**self.signal, "min_line_pivots": 3}
        self.assertIsNone(
            self.native.fit_pivot_line(
                [asdict(self._pivot(0, 100.0)), asdict(self._pivot(10, 101.0))],
                20,
                three_pivot_signal,
            )
        )

    def test_entry_channel_selection_projection_and_gate_match_python(self) -> None:
        far_support = self._zone("far-support", "support", 95.0)
        near_support = self._zone("near-support", "support", 100.0)
        near_support.slope_per_session = 0.25
        resistance = self._zone("resistance", "resistance", 110.0)
        resistance.slope_per_session = 0.5
        zones = [far_support, resistance, near_support]

        expected = _oracle_build_entry_channel(zones, 105.0, date(2025, 1, 2))
        actual = self.native.build_entry_channel(
            [zone.snapshot() for zone in zones],
            105.0,
            date(2025, 1, 2),
        )
        self._assert_value_equal(actual, expected)

        expected_projected = _oracle_project_entry_channel(expected)
        actual_projected = self.native.project_entry_channel(actual)
        self._assert_value_equal(actual_projected, expected_projected)
        for price in (101.25, 109.5, 109.5001, math.nan):
            with self.subTest(price=price):
                self.assertEqual(
                    self.native.entry_price_is_inside_channel(actual_projected, price),
                    _oracle_entry_price_is_inside_channel(expected_projected, price),
                )

    def test_entry_channel_failure_reasons_match_frozen_oracle(self) -> None:
        support = self._zone("support", "support", 104.0)
        resistance = self._zone("resistance", "resistance", 106.0)
        build_cases = (
            ([support], 105.0),
            ([resistance], 105.0),
            ([support, resistance], 105.0),
        )
        for zones, close in build_cases:
            with self.subTest(zones=[zone.zone_key for zone in zones], close=close):
                self._assert_value_equal(
                    self.native.build_entry_channel(
                        [zone.snapshot() for zone in zones],
                        close,
                        date(2025, 1, 2),
                    ),
                    _oracle_build_entry_channel(zones, close, date(2025, 1, 2)),
                )

        projection_cases = (
            None,
            {"valid": False, "reason_code": "missing_support_or_resistance"},
            {"valid": True, "lower": 100.0, "upper": 110.0},
            {
                "valid": True,
                "lower": 100.0,
                "upper": 110.0,
                "lower_slope_per_session": -101.0,
                "upper_slope_per_session": 0.0,
            },
            {
                "valid": True,
                "lower": 100.0,
                "upper": 110.0,
                "lower_slope_per_session": 10.0,
                "upper_slope_per_session": -10.0,
            },
        )
        for channel in projection_cases:
            with self.subTest(channel=channel):
                self._assert_value_equal(
                    self.native.project_entry_channel(channel),
                    _oracle_project_entry_channel(channel),
                )

    @staticmethod
    def _pivot(index: int, price: float) -> Pivot:
        return Pivot(
            pivot_key=f"low:{index}",
            kind="low",
            session_index=index,
            trade_date=date(2025, 1, 1) + timedelta(days=index),
            confirmed_on=date(2025, 1, 4) + timedelta(days=index),
            price=price,
            atr=1.0,
        )

    @staticmethod
    def _zone(key: str, role: str, center: float) -> Zone:
        return Zone(
            zone_key=key,
            source_kind="low" if role == "support" else "high",
            role=role,
            status="active",
            center=center,
            lower=center - 1.0,
            upper=center + 1.0,
            atr=2.0,
            pivot_keys=(f"{key}:1", f"{key}:2"),
            pivot_count=2,
            touch_count=2,
            first_pivot_date=date(2024, 12, 1),
            last_pivot_date=date(2024, 12, 20),
            valid_from=date(2024, 12, 23),
        )

    @staticmethod
    def _bar(offset: int, *, close: float) -> dict[str, object]:
        return {
            "dt_ny": date(2025, 1, 1) + timedelta(days=offset),
            "open": close,
            "high": max(close, 1.0),
            "low": min(close, 0.4),
            "close": close,
            "volume": 100.0,
            "volume_sma_20": 100.0,
            "atr_14": 1.0,
            "position": 0.0,
        }


if __name__ == "__main__":
    unittest.main()

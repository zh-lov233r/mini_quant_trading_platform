from __future__ import annotations

import copy
import unittest

from backend.tests import test_native_backtest_kernel as native_helpers
from src.services.strategy_engine import evaluate_native_signals
from src.services.strategy_registry import normalize_strategy_params


class BottomReversalRulesTests(unittest.TestCase):
    def setUp(self):
        self.helper = native_helpers.NativeBacktestKernelTests()
        self.cases = {kind: (bars, params) for kind, bars, params in self.helper._staged_pattern_cases()}

    def evaluate(self, kind, bars=None, params=None, entry=None, average=None):
        base_bars, base_params = self.cases[kind]
        bars = copy.deepcopy(base_bars if bars is None else bars)
        runtime = self.helper._pattern_runtime(kind, base_params if params is None else params)
        snapshots = self.helper._pattern_days(bars)[-1][1]
        if entry is not None:
            snapshots['TEST'].update(position=1.0, avg_entry_price=average or bars[-1]['close'], entry_signal_features=entry)
        return evaluate_native_signals(runtime, snapshots)

    def buy(self, kind, end):
        bars, params = self.cases[kind]
        signals = self.evaluate(kind, bars[:end], params)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, 'BUY')
        self.assertEqual(signals[0].ts.date(), bars[end - 1]['dt_ny'])
        return signals[0]

    def test_all_five_positive_stages_have_explicit_dates_and_targets(self):
        ends = {'island_reversal': (4, 5, 6), 'double_bottom': (9, 12, 13),
                'head_shoulders_bottom': (6, 8, 9), 'rounded_bottom': (88, 95, 101), 'v_reversal': (61, 63, 68)}
        for kind, lengths in ends.items():
            setups = []
            for stage, end in enumerate(lengths, 1):
                with self.subTest(kind=kind, stage=stage):
                    setup = self.buy(kind, end).metadata['setup']
                    self.assertEqual(setup['stage_index'], stage)
                    self.assertEqual(setup['stage_target_pct'], (.2, .5, 1.)[stage - 1])
                    self.assertGreater(setup['invalidation_price'], 0)
                    setups.append(setup['setup_id'])
            self.assertEqual(len(set(setups)), 1)

    def test_double_bottom_never_undercuts_first_and_tolerance_is_upward(self):
        for low, allowed in ((96.5, False), (98., True), (100.94, True), (100.95, False)):
            bars, params = copy.deepcopy(self.cases['double_bottom'])
            bars[7].update(low=low, close=max(101., low), high=102., open=101.5)
            with self.subTest(low=low):
                self.assertEqual(bool(self.evaluate('double_bottom', bars[:9], params)), allowed)

    def test_double_bottom_rejects_wick_only_breakout_and_bad_rebound_volume(self):
        bars, params = copy.deepcopy(self.cases['double_bottom'])
        bars[12]['close'] = 111.
        self.assertFalse(self.evaluate('double_bottom', bars[:13], params))
        for volume in (50., 200.):
            bad = copy.deepcopy(bars)
            bad[4]['volume'] = bad[5]['volume'] = volume
            self.assertFalse(self.evaluate('double_bottom', bad[:9], params))
        params['signal']['retest_window'] = 3
        self.assertFalse(self.evaluate('double_bottom', self.cases['double_bottom'][0][:12], params))

    def test_head_prerequisites_cannot_be_bypassed_at_later_stages(self):
        for failure in ('head_volume', 'downtrend', 'platform', 'recovery_volume', 'recovery_range', 'missing_atr'):
            bars, params = copy.deepcopy(self.cases['head_shoulders_bottom'])
            if failure == 'head_volume': bars[4]['volume'] = 200.
            elif failure == 'downtrend': params['signal']['downtrend_min_drop_pct'] = .8
            elif failure == 'platform': params['signal']['platform_range_atr_max'] = .1
            elif failure == 'recovery_volume': bars[5]['volume'] = 50.
            elif failure == 'recovery_range': bars[5].update(open=14., close=15., high=15.5)
            else:
                for bar in bars: bar['atr_14'] = None
            for end in (8, 9):
                with self.subTest(failure=failure, end=end):
                    self.assertFalse(self.evaluate('head_shoulders_bottom', bars[:end], params))

    def test_head_breakout_on_shoulder_confirmation_uses_stage_three(self):
        bars, params = copy.deepcopy(self.cases['head_shoulders_bottom'])
        bars[7].update(open=14., high=16., low=13.9, close=15.5, volume=160.)
        signals = self.evaluate('head_shoulders_bottom', bars[:8], params)
        self.assertEqual([s.metadata['setup']['stage_index'] for s in signals], [3])

    def test_v_retest_requires_touch_and_unbroken_intermediate_support(self):
        bars, params = copy.deepcopy(self.cases['v_reversal'])
        self.assertEqual(self.buy('v_reversal', 68).metadata['setup']['consolidation_top'], 98.5)
        bars[67].update(open=106., high=108., low=105., close=107., volume=100.)
        self.assertFalse(self.evaluate('v_reversal', bars[:68], params))
        bars[67].update(open=98., high=99., low=96., close=96., volume=100.)
        self.assertFalse(self.evaluate('v_reversal', bars[:69], params))
        params.setdefault('signal', {})['consolidation_range_atr_max'] = .1
        self.assertFalse(self.evaluate('v_reversal', self.cases['v_reversal'][0][:68], params))

    def test_v_delayed_turn_and_continuation_must_exceed_reversal_close(self):
        bars, params = copy.deepcopy(self.cases['v_reversal'])
        bars[60].update(open=93., high=94., low=90., close=91., volume=100.)
        bars[61].update(open=93., high=96., low=92., close=95., volume=220.)
        setup = self.evaluate('v_reversal', bars[:62], params)[0].metadata['setup']
        self.assertEqual(setup['stage_index'], 1)
        self.assertEqual(setup['anchors']['pivot'], bars[60]['dt_ny'].isoformat())
        self.assertEqual(setup['anchors']['reversal'], bars[61]['dt_ny'].isoformat())
        bars, params = copy.deepcopy(self.cases['v_reversal'])
        bars[61].update(open=92., close=94.)
        self.assertFalse(self.evaluate('v_reversal', bars[:63], params))

    def test_v_more_high_volume_days_do_not_restart_the_same_reversal(self):
        bars, params = copy.deepcopy(self.cases['v_reversal'])
        bars[61]['volume'] = bars[62]['volume'] = 220.
        event = self.evaluate('v_reversal', bars[:63], params)[0]
        self.assertEqual((event.action, event.metadata['setup']['stage_index']), ('BUY', 2))
        self.assertEqual(event.metadata['setup']['anchors']['reversal'], bars[60]['dt_ny'].isoformat())

    def test_v_bearish_exit_requires_large_body_and_two_prior_volume_up_days(self):
        entry = self.buy('v_reversal', 63).metadata
        for body, volume, expected in ((2., 220., True), (.2, 220., False), (2., 150., False)):
            bars, params = copy.deepcopy(self.cases['v_reversal'])
            bars[63].update(open=97., close=97.-body, high=98., low=94., volume=volume)
            signals = self.evaluate('v_reversal', bars[:64], params, entry=entry, average=97.)
            sells = [s for s in signals if s.action == 'SELL']
            self.assertEqual(bool(sells), expected)
            if expected: self.assertEqual(sells[0].metadata['setup']['exit_stage'], 'bearish_volume_failure')

    def test_island_candle_requirements_and_sma_cannot_replace_downtrend(self):
        for failure in ('previous_bull', 'previous_small', 'exhaustion_large', 'breakout_small', 'no_drop', 'missing_atr'):
            bars, params = copy.deepcopy(self.cases['island_reversal'])
            if failure == 'previous_bull': bars[2]['open'] = 99.
            elif failure == 'previous_small': bars[2]['open'] = 100.1
            elif failure == 'exhaustion_large': bars[3]['open'] = 95.5
            elif failure == 'breakout_small': bars[4]['open'] = 101.5
            elif failure == 'no_drop':
                params['signal']['downtrend_min_drop_pct'] = .9
                for bar in bars: bar['sma_50'] = 200.
            else: bars[3]['atr_14'] = None
            with self.subTest(failure=failure): self.assertFalse(self.evaluate('island_reversal', bars[:5], params))

    def test_rounded_third_and_fourth_pullbacks_stay_at_stage_two(self):
        bars, params = copy.deepcopy(self.cases['rounded_bottom'])
        # Extend the same smooth bowl; lows 99/106 are separated by seven sessions.
        import math
        for offset in range(95, 109):
            close = math.exp(math.log(80) + (math.log(110) - math.log(80)) / .25 * (offset / 100 - .5) ** 2)
            bar = self.helper._pattern_bar(offset, close * .995, close * 1.01, close * .99, close,
                                          140. if offset in (97, 104) else 70. if offset in (99, 106) else 100.)
            if offset in (99, 106): bar['low'] = close * .94
            if offset < len(bars): bars[offset] = bar
            else: bars.append(bar)
        for count, end in ((3, 102), (4, 109)):
            signals = self.evaluate('rounded_bottom', bars[:end], params)
            self.assertEqual([s.metadata['setup']['stage_index'] for s in signals], [2])
            self.assertEqual(signals[0].metadata['setup']['stage_target_pct'], .5)
            self.assertEqual(len(signals[0].metadata['setup']['anchors']['pullbacks']), count)

    def test_rounded_weakness_exits_before_generic_stop(self):
        entry = self.buy('rounded_bottom', 95).metadata
        bars, params = copy.deepcopy(self.cases['rounded_bottom'])
        bars = bars[:95]
        for index, (high, low, close) in enumerate(((108,103,106),(109,103,107),(107,102,105),
                (106,101,104),(107,102,105),(106,102,104),(105,102,104),(104,100,100.4)),95):
            bars.append(self.helper._pattern_bar(index, close+.1, high, low, close, 100., atr=10.))
        signals = self.evaluate('rounded_bottom', bars, params, entry=entry, average=104.)
        self.assertEqual([s.action for s in signals], ['SELL'])
        self.assertEqual(signals[0].metadata['setup']['exit_stage'], 'right_side_failure')
        threshold = signals[0].metadata['setup']['failure_support_price'] * .995
        bars[-1]['close'] = threshold
        self.assertEqual(self.evaluate('rounded_bottom', bars, params, entry, 104.)[0].action, 'SELL')
        bars[-1].update(close=104., low=102.)
        self.assertFalse(any(s.action == 'SELL' for s in self.evaluate('rounded_bottom', bars, params, entry=entry, average=104.)))

    def test_head_default_five_bar_platform_uses_latest_ending_window(self):
        base, params = copy.deepcopy(self.cases['head_shoulders_bottom'])
        params['signal'].pop('platform_bars')
        params['signal']['downtrend_lookback'] = 6
        rows = [base[0], base[1], self.helper._pattern_bar(2, 10.9, 11.5, 10.5, 10.8, 100.),
                self.helper._pattern_bar(3, 10.7, 11.2, 10.3, 10.6, 100.), base[2], base[3],
                self.helper._pattern_bar(6, 11., 12., 10.8, 11., 100.), *base[4:]]
        from datetime import timedelta
        for index, row in enumerate(rows): row['dt_ny'] = base[0]['dt_ny'] + timedelta(days=index)
        event = self.evaluate('head_shoulders_bottom', rows[:12], params)[0]
        self.assertEqual((event.action, event.metadata['setup']['stage_index']), ('BUY', 3))
        self.assertEqual(event.metadata['setup']['anchors']['platform_start'], rows[2]['dt_ny'].isoformat())
        self.assertEqual(event.metadata['setup']['anchors']['platform_end'], rows[6]['dt_ny'].isoformat())
        self.assertEqual(event.metadata['setup']['platform_low'], 10.)
        self.assertEqual(event.metadata['setup']['platform_high'], 13.)

    def test_v_valid_breakout_disables_special_bearish_exit(self):
        entry = self.buy('v_reversal', 63).metadata
        bars, params = copy.deepcopy(self.cases['v_reversal'])
        bars[67].update(open=100., close=101., volume=120.)
        bars[68].update(open=102., high=103., low=99., close=100., volume=220.)
        self.assertFalse(any(s.action == 'SELL' for s in self.evaluate('v_reversal', bars, params, entry, 99.)))

    def test_island_real_gaps_middle_body_and_retest_failure(self):
        base, params = copy.deepcopy(self.cases['island_reversal'])
        for field, value in (('high', 100.), ('volume', 150.)):
            bars = copy.deepcopy(base)
            bars[3][field] = value
            self.assertFalse(self.evaluate('island_reversal', bars[:5], params))
        for field, value in (('low', 95.), ('volume', 100.), ('open', 103.)):
            bars = copy.deepcopy(base)
            bars[4][field] = value
            self.assertFalse(self.evaluate('island_reversal', bars[:5], params))
        from datetime import timedelta
        middle = self.helper._pattern_bar(4, 94., 95., 92.5, 94.5, 70.)
        bars = [*base[:4], middle, base[4]]
        bars[-1]['dt_ny'] += timedelta(days=1)
        self.assertEqual(self.evaluate('island_reversal', bars, params)[0].metadata['setup']['stage_index'], 2)
        bars[4].update(close=95.5, high=96.)  # 1.5 / ATR2 exceeds the middle-body cap.
        self.assertFalse(self.evaluate('island_reversal', bars, params))
        bars = copy.deepcopy(self.cases['island_reversal'][0])
        bars[5]['low'] = 90.
        self.assertFalse(self.evaluate('island_reversal', bars[:6], params))

    def test_stage_reduction_preserves_sell_and_allows_equal_stage_retry(self):
        from types import SimpleNamespace
        from src.services.staged_entry_service import can_apply_staged_entry, select_highest_stage_signals
        event = self.buy('rounded_bottom', 95)
        low = copy.deepcopy(event.metadata)
        low['setup']['stage_index'] = 1
        sell = SimpleNamespace(action='SELL', metadata=low, symbol='TEST', instrument_id=1)
        first = SimpleNamespace(action='BUY', metadata=low, symbol='TEST', instrument_id=1)
        second = SimpleNamespace(action='BUY', metadata=event.metadata, symbol='TEST', instrument_id=1)
        self.assertEqual(select_highest_stage_signals([first, sell, second]), [sell, second])
        self.assertTrue(can_apply_staged_entry(event.metadata, event.metadata))
        self.assertFalse(can_apply_staged_entry(low, event.metadata))

    def test_close_signal_fills_at_next_available_session_open_when_entering_late(self):
        import quant_kernel
        bars, params = self.cases['double_bottom']
        # Warm up the detector through the right bottom, then begin at final confirmation.
        dataset = self.helper._dataset(self.helper._pattern_days(bars))
        result = quant_kernel.run_backtest(dataset, self.helper._pattern_runtime('double_bottom', params),
            {'start_date': bars[12]['dt_ny'], 'initial_cash': 1000., 'commission_bps': 0., 'commission_min': 0., 'slippage_bps': 0.})
        self.assertEqual(result.trades['stage_index'].tolist(), [3])
        self.assertEqual(result.trades['session_index'].tolist(), [13])
        self.assertEqual(result.trades['price'].tolist(), [114.])

    def test_descriptor_rejects_invalid_new_thresholds(self):
        for kind, signal in (('island_reversal', {'previous_body_atr_min': 0}),
                             ('head_shoulders_bottom', {'platform_bars': 2}),
                             ('double_bottom', {'rebound_volume_ratio_min': 2., 'rebound_volume_ratio_max': 1.}),
                             ('rounded_bottom', {'weakening_buffer_pct': 1.}),
                             ('v_reversal', {'consolidation_range_atr_max': -1.})):
            with self.subTest(kind=kind), self.assertRaises(ValueError): normalize_strategy_params(kind, {'signal': signal})

    def test_descriptor_accepts_integer_zero_for_zero_allowed_decimal_threshold(self):
        self.assertEqual(normalize_strategy_params('v_reversal', {'signal': {'breakout_buffer_pct': 0}})['signal']['breakout_buffer_pct'], 0)
        params = normalize_strategy_params('v_reversal', {'metadata': {'algorithm_version': 'volume-v-reversal-v1'}})
        self.assertEqual(params['metadata']['algorithm_version'], 'volume-v-reversal-v2')

    def test_future_suffix_does_not_change_prefix_backtest_signals(self):
        for kind, bars, params in self.helper._staged_pattern_cases():
            runtime = self.helper._pattern_runtime(kind, params)
            full = self.helper._run_native(self.helper._pattern_days(bars), runtime)
            self.assertGreater(len(full.signals['action']), 0)
            for end in range(1, len(bars)):
                prefix = self.helper._run_native(self.helper._pattern_days(bars[:end]), runtime)
                expected = [value for i, value in enumerate(full.signals['metadata_json']) if full.signals['session_index'][i] < end]
                self.assertEqual(list(prefix.signals['metadata_json']), expected, (kind, end))


if __name__ == '__main__':
    unittest.main()

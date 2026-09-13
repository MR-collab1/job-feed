"""Cost accounting and equity arithmetic."""

import unittest

from crypto.backtest import (
    CostModel,
    SizingConfig,
    break_even_accuracy,
    buy_and_hold,
    run_backtest,
    size_positions,
)
from crypto.metrics import equity_curve, max_drawdown, sharpe_ratio, summarize

FREE = CostModel(fee_bps=0.0, slippage_bps=0.0, borrow_bps_annual=0.0)
RETAIL = CostModel(fee_bps=10.0, slippage_bps=5.0, borrow_bps_annual=0.0)


class TestCosts(unittest.TestCase):
    def test_entry_costs_one_side_and_holding_is_free(self):
        result = run_backtest([1.0, 1.0, 1.0], [0.01, 0.01, 0.01], 365.25, RETAIL)
        self.assertAlmostEqual(result.costs[0], 0.0015)  # 15 bps to get in
        self.assertAlmostEqual(result.costs[1], 0.0)
        self.assertAlmostEqual(result.costs[2], 0.0)

    def test_flipping_long_to_short_costs_two_units_of_notional(self):
        result = run_backtest([1.0, -1.0], [0.0, 0.0], 365.25, RETAIL)
        self.assertAlmostEqual(result.costs[1], 2 * 0.0015)

    def test_half_size_position_pays_half_the_cost(self):
        full = run_backtest([1.0], [0.0], 365.25, RETAIL)
        half = run_backtest([0.5], [0.0], 365.25, RETAIL)
        self.assertAlmostEqual(half.costs[0], full.costs[0] / 2)

    def test_short_pays_financing_and_long_does_not(self):
        # 100 bps a year over 365.25 daily periods is 1/365.25 bps a day.
        model = CostModel(fee_bps=0.0, slippage_bps=0.0, borrow_bps_annual=100.0)
        expected = (100.0 / 10_000.0) / 365.25
        short = run_backtest([-1.0], [0.0], 365.25, model)
        long = run_backtest([1.0], [0.0], 365.25, model)
        self.assertAlmostEqual(short.costs[0], expected, places=10)
        self.assertAlmostEqual(long.costs[0], 0.0)

    def test_costs_reduce_returns(self):
        free = run_backtest([1.0, 0.0, 1.0], [0.02, 0.02, 0.02], 365.25, FREE)
        paid = run_backtest([1.0, 0.0, 1.0], [0.02, 0.02, 0.02], 365.25, RETAIL)
        self.assertGreater(sum(free.net_returns), sum(paid.net_returns))

    def test_churn_turns_a_winning_signal_into_a_loser(self):
        """A genuine 60% hit rate on 0.1% moves is still a loss at 15 bps a side.

        This is the arithmetic that sinks most high-frequency retail ideas, so
        it is pinned down as a test rather than left as a warning in a docstring.
        """
        positions = [1.0 if i % 2 == 0 else -1.0 for i in range(200)]
        # The position is right on 6 bars out of every 10 -- a real 60% edge.
        forwards = [positions[i] * (0.001 if i % 10 < 6 else -0.001) for i in range(200)]
        gross = run_backtest(positions, forwards, 365.25, FREE)
        net = run_backtest(positions, forwards, 365.25, RETAIL)
        self.assertGreater(sum(gross.gross_returns), 0.0, "the signal itself should win")
        self.assertLess(sum(net.net_returns), 0.0, "costs should still bury it")


class TestMechanics(unittest.TestCase):
    def test_zero_position_earns_and_costs_nothing(self):
        result = run_backtest([0.0] * 5, [0.5, -0.5, 0.3, -0.9, 0.1], 365.25, RETAIL)
        self.assertEqual(sum(result.net_returns), 0.0)
        self.assertEqual(result.exposure, 0.0)

    def test_equity_compounds(self):
        curve = equity_curve([0.1, 0.1])
        self.assertAlmostEqual(curve[-1], 1.21)

    def test_length_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            run_backtest([1.0, 1.0], [0.01], 365.25)

    def test_buy_and_hold_tracks_the_asset(self):
        forwards = [0.05, -0.02, 0.03]
        result = buy_and_hold(forwards, 365.25, FREE)
        self.assertAlmostEqual(result.performance.total_return, 1.05 * 0.98 * 1.03 - 1.0)

    def test_turnover_is_annualized(self):
        """Flipping every bar on daily data is ~365 units of notional a year."""
        positions = [1.0 if i % 2 == 0 else -1.0 for i in range(100)]
        result = run_backtest(positions, [0.0] * 100, 365.0, FREE)
        self.assertAlmostEqual(result.turnover, 2.0 * 365.0, delta=10.0)

    def test_initial_position_avoids_a_phantom_entry_cost(self):
        already_long = run_backtest([1.0], [0.0], 365.25, RETAIL, initial_position=1.0)
        self.assertAlmostEqual(already_long.costs[0], 0.0)


class TestSizing(unittest.TestCase):
    def test_long_only_never_goes_short(self):
        positions = size_positions([0.1, 0.3, 0.9], SizingConfig(allow_short=False))
        self.assertTrue(all(p >= 0 for p in positions))

    def test_band_keeps_weak_signals_flat(self):
        positions = size_positions([0.51, 0.49], SizingConfig(band=0.05, allow_short=True))
        self.assertEqual(positions, [0.0, 0.0])

    def test_proportional_mode_scales_with_conviction(self):
        config = SizingConfig(mode="proportional", band=0.02, allow_short=True)
        weak, strong = size_positions([0.53, 0.75], config)
        self.assertLess(abs(weak), abs(strong))
        self.assertLessEqual(abs(strong), 1.0)

    def test_max_position_caps_leverage(self):
        positions = size_positions([0.99], SizingConfig(max_position=0.5))
        self.assertLessEqual(positions[0], 0.5)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            size_positions([0.9], SizingConfig(mode="nonsense"))

    def test_vol_targeting_cuts_size_in_turbulent_markets(self):
        config = SizingConfig(target_vol_annual=0.30, allow_short=True)
        calm = size_positions([0.9], config, vol_estimate=[0.30])
        wild = size_positions([0.9], config, vol_estimate=[1.20])
        self.assertGreater(calm[0], wild[0])


class TestBreakEven(unittest.TestCase):
    def test_free_trading_breaks_even_at_a_coin_flip(self):
        self.assertAlmostEqual(break_even_accuracy(FREE, 0.03, 1.0), 0.5)

    def test_costs_raise_the_bar(self):
        self.assertGreater(break_even_accuracy(RETAIL, 0.03, 1.0), 0.5)

    def test_small_moves_make_the_bar_unreachable(self):
        """Scalping tiny moves at retail fees needs an impossible hit rate."""
        self.assertGreaterEqual(break_even_accuracy(RETAIL, 0.0001, 1.0), 0.99)


class TestMetrics(unittest.TestCase):
    def test_constant_returns_have_no_drawdown(self):
        self.assertEqual(max_drawdown([0.01] * 20), 0.0)

    def test_known_drawdown(self):
        self.assertAlmostEqual(max_drawdown([0.5, -0.5]), -0.5)

    def test_zero_volatility_gives_zero_sharpe_not_an_error(self):
        self.assertEqual(sharpe_ratio([0.0] * 10, 365.25), 0.0)

    def test_annualization_scales_with_frequency(self):
        returns = [0.001, 0.002, -0.001, 0.003] * 25
        daily = summarize(returns, 365.25).sharpe
        hourly = summarize(returns, 365.25 * 24).sharpe
        self.assertAlmostEqual(hourly / daily, 24 ** 0.5, places=6)

    def test_empty_returns_do_not_crash(self):
        performance = summarize([], 365.25)
        self.assertEqual(performance.periods, 0)


if __name__ == "__main__":
    unittest.main()

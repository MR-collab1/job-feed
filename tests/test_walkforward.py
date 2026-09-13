"""Fold construction, evaluation integrity, and the significance tools."""

import unittest

from crypto.backtest import CostModel, SizingConfig
from crypto.data import synthetic_bars, periods_per_year
from crypto.features import build_dataset
from crypto.significance import (
    block_bootstrap_sharpe,
    expected_max_sharpe_under_null,
    permutation_test,
    probabilistic_sharpe_ratio,
    sharpe_standard_error,
)
from crypto.walkforward import (
    WalkForwardConfig,
    backtest_walk_forward,
    make_folds,
    run_walk_forward,
)


class TestFolds(unittest.TestCase):
    def test_training_always_precedes_testing(self):
        for train_start, train_end, test_start, test_end in make_folds(
            1000, WalkForwardConfig(train_size=300, test_size=40)
        ):
            self.assertLess(train_end, test_start, "test data must come after training data")
            self.assertLess(test_start, test_end)

    def test_embargo_is_respected(self):
        folds = make_folds(1000, WalkForwardConfig(train_size=300, test_size=40, embargo=5))
        for _, train_end, test_start, _ in folds:
            self.assertEqual(test_start - train_end, 5)

    def test_test_windows_do_not_overlap(self):
        folds = make_folds(1000, WalkForwardConfig(train_size=300, test_size=40))
        covered = []
        for _, _, test_start, test_end in folds:
            covered.extend(range(test_start, test_end))
        self.assertEqual(len(covered), len(set(covered)), "a bar was scored twice")

    def test_folds_stay_inside_the_data(self):
        n = 500
        for _, _, _, test_end in make_folds(n, WalkForwardConfig(train_size=200, test_size=40)):
            self.assertLessEqual(test_end, n)

    def test_no_folds_when_history_is_too_short(self):
        self.assertEqual(make_folds(100, WalkForwardConfig(train_size=300, test_size=40)), [])

    def test_absurd_windows_are_rejected(self):
        with self.assertRaises(ValueError):
            make_folds(1000, WalkForwardConfig(train_size=5, test_size=40))
        with self.assertRaises(ValueError):
            make_folds(1000, WalkForwardConfig(train_size=300, test_size=0))


class TestWalkForwardRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = synthetic_bars(n=500, seed=21)
        cls.dataset = build_dataset(cls.bars)
        cls.config = WalkForwardConfig(train_size=200, test_size=50, iterations=150)
        cls.result = run_walk_forward(cls.dataset, cls.config)

    def test_every_prediction_is_out_of_sample(self):
        self.assertEqual(
            self.result.out_of_sample_size, len(self.result.forward_returns)
        )
        for fold in self.result.folds:
            self.assertGreaterEqual(fold.test_start, fold.train_end + self.config.embargo)

    def test_predictions_align_with_their_forward_returns(self):
        for k, sample in enumerate(self.result.sample_indices):
            self.assertEqual(
                self.result.forward_returns[k], self.dataset.forward_return[sample]
            )
            self.assertEqual(self.result.labels[k], self.dataset.y[sample])

    def test_out_of_sample_accuracy_is_not_absurd(self):
        """An unpredictable series should score near a coin flip, never near 1.0."""
        self.assertLess(self.result.directional_accuracy(), 0.62)

    def test_too_little_data_raises_a_useful_error(self):
        small = build_dataset(synthetic_bars(n=120, seed=1))
        with self.assertRaises(ValueError) as context:
            run_walk_forward(small, WalkForwardConfig(train_size=400, test_size=50))
        self.assertIn("too few", str(context.exception))

    def test_backtest_wires_up_to_the_walk_forward_result(self):
        result = backtest_walk_forward(
            self.result, SizingConfig(), CostModel(), 365.25
        )
        self.assertEqual(len(result.positions), self.result.out_of_sample_size)


class TestSignificance(unittest.TestCase):
    def test_a_worthless_strategy_is_not_significant(self):
        bars = synthetic_bars(n=600, seed=31, signal_strength=0.0)
        dataset = build_dataset(bars)
        ppy = periods_per_year(bars)
        result = run_walk_forward(
            dataset, WalkForwardConfig(train_size=250, test_size=50, iterations=150)
        )
        backtest = backtest_walk_forward(
            result, SizingConfig(allow_short=True), CostModel(), ppy
        )
        permutation = permutation_test(
            backtest.positions, result.forward_returns, ppy, CostModel(), trials=120
        )
        self.assertGreater(permutation.p_value, 0.05)
        self.assertFalse(permutation.significant)

    def test_a_real_edge_is_detected(self):
        """Guards against a harness that can only ever say 'no edge'."""
        bars = synthetic_bars(n=1000, seed=5, signal_strength=0.25)
        dataset = build_dataset(bars)
        ppy = periods_per_year(bars)
        result = run_walk_forward(
            dataset, WalkForwardConfig(train_size=400, test_size=50, iterations=250)
        )
        backtest = backtest_walk_forward(
            result, SizingConfig(allow_short=True), CostModel(), ppy
        )
        permutation = permutation_test(
            backtest.positions, result.forward_returns, ppy, CostModel(), trials=200
        )
        self.assertGreater(result.directional_accuracy(), 0.52)
        self.assertGreater(backtest.performance.sharpe, 0.5)
        self.assertLess(permutation.p_value, 0.05)

    def test_p_value_is_never_exactly_zero(self):
        permutation = permutation_test([1.0] * 50, [0.01] * 50, 365.25, CostModel(), trials=20)
        self.assertGreater(permutation.p_value, 0.0)
        self.assertLessEqual(permutation.p_value, 1.0)

    def test_bootstrap_interval_is_ordered_and_wide_on_noise(self):
        import random

        rng = random.Random(2)
        returns = [rng.gauss(0.0, 0.03) for _ in range(500)]
        low, high = block_bootstrap_sharpe(returns, 365.25, trials=200)
        self.assertLess(low, high)
        self.assertLess(low, 0.0)

    def test_bootstrap_handles_a_short_series(self):
        self.assertEqual(block_bootstrap_sharpe([0.01] * 5, 365.25), (0.0, 0.0))

    def test_probabilistic_sharpe_is_a_probability(self):
        import random

        rng = random.Random(4)
        returns = [rng.gauss(0.001, 0.02) for _ in range(400)]
        psr = probabilistic_sharpe_ratio(returns, 365.25)
        self.assertGreaterEqual(psr, 0.0)
        self.assertLessEqual(psr, 1.0)

    def test_more_trials_raise_the_luck_benchmark(self):
        error = sharpe_standard_error(500, 365.25)
        self.assertLess(
            expected_max_sharpe_under_null(5, error),
            expected_max_sharpe_under_null(500, error),
        )

    def test_a_single_trial_has_no_selection_bias(self):
        self.assertEqual(expected_max_sharpe_under_null(1, 0.5), 0.0)


if __name__ == "__main__":
    unittest.main()

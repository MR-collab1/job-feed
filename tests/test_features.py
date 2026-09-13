"""The tests that matter most: proving no future information leaks backwards."""

import unittest

from crypto.data import Bar, synthetic_bars
from crypto.features import WARMUP, build_dataset, feature_row


class TestCausality(unittest.TestCase):
    def setUp(self):
        self.bars = synthetic_bars(n=200, seed=42)

    def test_feature_row_ignores_future_bars(self):
        """Truncating everything after bar i must not change the features at i."""
        for i in (WARMUP, WARMUP + 25, len(self.bars) - 5):
            full = feature_row(self.bars, i)
            truncated = feature_row(self.bars[: i + 1], i)
            self.assertEqual(full, truncated, f"features at bar {i} depend on the future")

    def test_corrupting_a_future_bar_changes_nothing(self):
        """A 100x price spike tomorrow must not move today's feature vector."""
        i = 120
        before = feature_row(self.bars, i)
        tampered = list(self.bars)
        future = tampered[i + 1]
        tampered[i + 1] = Bar(future.ts, future.open * 100, future.high * 100,
                              future.low * 100, future.close * 100, future.volume * 100)
        after = feature_row(tampered, i)
        self.assertEqual(before, after)

    def test_warmup_is_enforced(self):
        with self.assertRaises(ValueError):
            feature_row(self.bars, WARMUP - 1)

    def test_all_features_are_finite(self):
        for i in range(WARMUP, len(self.bars)):
            for name, value in zip(range(99), feature_row(self.bars, i)):
                self.assertFalse(value != value, "NaN in feature vector")
                self.assertLess(abs(value), 1e9)

    def test_flat_market_produces_no_nans(self):
        """Zero range and zero volume must not divide by zero."""
        flat = [Bar(ts=i * 86400, open=100.0, high=100.0, low=100.0, close=100.0, volume=0.0)
                for i in range(120)]
        row = feature_row(flat, 100)
        self.assertTrue(all(v == v for v in row))


class TestDatasetAlignment(unittest.TestCase):
    def setUp(self):
        self.bars = synthetic_bars(n=180, seed=9)
        self.dataset = build_dataset(self.bars)

    def test_forward_return_is_the_next_bar(self):
        for k, i in enumerate(self.dataset.bar_index):
            expected = self.bars[i + 1].close / self.bars[i].close - 1.0
            self.assertAlmostEqual(self.dataset.forward_return[k], expected, places=12)

    def test_label_matches_forward_return_sign(self):
        for k in range(len(self.dataset)):
            expected = 1 if self.dataset.forward_return[k] > 0 else 0
            self.assertEqual(self.dataset.y[k], expected)

    def test_timestamp_is_the_decision_bar_not_the_outcome_bar(self):
        for k, i in enumerate(self.dataset.bar_index):
            self.assertEqual(self.dataset.timestamp[k], self.bars[i].ts)

    def test_last_bar_is_excluded(self):
        """The final bar has no known future, so it must not become a sample."""
        self.assertEqual(self.dataset.bar_index[-1], len(self.bars) - 2)

    def test_sample_count(self):
        self.assertEqual(len(self.dataset), len(self.bars) - WARMUP - 1)

    def test_rejects_too_little_history(self):
        with self.assertRaises(ValueError):
            build_dataset(self.bars[:10])


if __name__ == "__main__":
    unittest.main()

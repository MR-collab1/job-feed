"""The estimator and the leakage-free scaling around it."""

import random
import unittest

from crypto.model import LogisticRegression, Pipeline, Standardizer, accuracy, log_loss, sigmoid


class TestSigmoid(unittest.TestCase):
    def test_midpoint(self):
        self.assertAlmostEqual(sigmoid(0.0), 0.5)

    def test_no_overflow_at_extremes(self):
        self.assertAlmostEqual(sigmoid(1000.0), 1.0)
        self.assertAlmostEqual(sigmoid(-1000.0), 0.0)


class TestStandardizer(unittest.TestCase):
    def test_training_columns_become_zero_mean_unit_scale(self):
        rows = [[float(i), float(i) * 3 + 1] for i in range(50)]
        scaled = Standardizer().fit_transform(rows)
        for j in range(2):
            column = [row[j] for row in scaled]
            self.assertAlmostEqual(sum(column) / len(column), 0.0, places=9)

    def test_constant_column_does_not_divide_by_zero(self):
        scaled = Standardizer().fit_transform([[5.0], [5.0], [5.0]])
        self.assertEqual([row[0] for row in scaled], [0.0, 0.0, 0.0])

    def test_test_data_uses_training_statistics(self):
        """A scaler refit on the test fold would leak the test distribution."""
        scaler = Standardizer().fit([[0.0], [2.0]])
        transformed = scaler.transform([[100.0]])
        self.assertGreater(transformed[0][0], 1.0)

    def test_extreme_values_are_clipped(self):
        scaler = Standardizer().fit([[0.0], [1.0]])
        self.assertLessEqual(scaler.transform([[1e9]])[0][0], 8.0)

    def test_empty_fit_is_rejected(self):
        with self.assertRaises(ValueError):
            Standardizer().fit([])


class TestLogisticRegression(unittest.TestCase):
    def setUp(self):
        rng = random.Random(0)
        self.X, self.y = [], []
        for _ in range(400):
            a, b, noise = rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1)
            self.X.append([a, b, noise])
            self.y.append(1 if 1.5 * a - 1.0 * b + rng.gauss(0, 0.3) > 0 else 0)

    def test_recovers_a_known_signal(self):
        model = LogisticRegression(l2=0.01, iterations=600).fit(self.X, self.y)
        self.assertGreater(accuracy(model.predict(self.X), self.y), 0.85)

    def test_recovers_coefficient_signs(self):
        model = LogisticRegression(l2=0.01, iterations=600).fit(self.X, self.y)
        self.assertGreater(model.weights[0], 0)   # a moves the label up
        self.assertLess(model.weights[1], 0)      # b moves it down
        self.assertLess(abs(model.weights[2]), abs(model.weights[0]) / 3)  # noise stays small

    def test_probabilities_stay_in_range(self):
        model = LogisticRegression().fit(self.X, self.y)
        for p in model.predict_proba(self.X):
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)

    def test_pure_noise_stays_near_the_base_rate(self):
        """No signal means predictions should hug 50%, not invent conviction."""
        rng = random.Random(3)
        X = [[rng.gauss(0, 1) for _ in range(5)] for _ in range(300)]
        y = [rng.randint(0, 1) for _ in range(300)]
        model = Pipeline(l2=10.0, iterations=300).fit(X, y)
        for p in model.predict_proba(X):
            self.assertLess(abs(p - 0.5), 0.25)

    def test_stronger_regularization_shrinks_weights(self):
        weak = LogisticRegression(l2=0.01, iterations=400).fit(self.X, self.y)
        strong = LogisticRegression(l2=100.0, iterations=400).fit(self.X, self.y)
        self.assertLess(
            sum(abs(w) for w in strong.weights), sum(abs(w) for w in weak.weights)
        )

    def test_mismatched_lengths_are_rejected(self):
        with self.assertRaises(ValueError):
            LogisticRegression().fit([[1.0], [2.0]], [1])

    def test_empty_fit_is_rejected(self):
        with self.assertRaises(ValueError):
            LogisticRegression().fit([], [])


class TestPipeline(unittest.TestCase):
    def test_coefficients_are_ranked_by_magnitude(self):
        rng = random.Random(5)
        X = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(200)]
        y = [1 if row[0] > 0 else 0 for row in X]
        pipeline = Pipeline(l2=0.01, iterations=300).fit(X, y)
        ranked = pipeline.coefficients(["strong", "weak"])
        self.assertEqual(ranked[0][0], "strong")

    def test_log_loss_rewards_calibration(self):
        confident_right = log_loss([0.9, 0.9], [1, 1])
        confident_wrong = log_loss([0.9, 0.9], [0, 0])
        self.assertLess(confident_right, confident_wrong)


if __name__ == "__main__":
    unittest.main()

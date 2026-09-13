"""A small, transparent classifier: standardization + L2 logistic regression.

Deliberately simple. With a few thousand noisy bars and fifteen correlated
features, a deep model mostly buys you a better-looking in-sample fit and a
worse out-of-sample one. The scoring machinery in this package matters far
more than the estimator does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple


def sigmoid(z: float) -> float:
    """Numerically stable logistic function."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass
class Standardizer:
    """Z-scores each column. Fit on training data only, never on the test fold."""

    mean: List[float] = field(default_factory=list)
    scale: List[float] = field(default_factory=list)

    def fit(self, X: Sequence[Sequence[float]]) -> "Standardizer":
        if not X:
            raise ValueError("cannot fit a standardizer on an empty matrix")
        n_cols = len(X[0])
        n_rows = len(X)
        self.mean = []
        self.scale = []
        for j in range(n_cols):
            column = [row[j] for row in X]
            mu = sum(column) / n_rows
            var = sum((v - mu) ** 2 for v in column) / max(1, n_rows - 1)
            sd = math.sqrt(var)
            self.mean.append(mu)
            # A constant column contributes nothing; scale of 1.0 leaves it at 0.
            self.scale.append(sd if sd > 1e-12 else 1.0)
        return self

    def transform(self, X: Sequence[Sequence[float]]) -> List[List[float]]:
        out = []
        for row in X:
            out.append(
                [
                    _clip((row[j] - self.mean[j]) / self.scale[j], -8.0, 8.0)
                    for j in range(len(self.mean))
                ]
            )
        return out

    def fit_transform(self, X: Sequence[Sequence[float]]) -> List[List[float]]:
        return self.fit(X).transform(X)


def _clip(value: float, low: float, high: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(low, min(high, value))


@dataclass
class LogisticRegression:
    """Batch gradient descent with L2 regularization."""

    l2: float = 1.0
    learning_rate: float = 0.2
    iterations: int = 400
    tolerance: float = 1e-7
    weights: List[float] = field(default_factory=list)
    bias: float = 0.0
    n_iter_: int = 0

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]) -> "LogisticRegression":
        if len(X) != len(y):
            raise ValueError("X and y must have the same length")
        if not X:
            raise ValueError("cannot fit on an empty training set")

        n, d = len(X), len(X[0])
        self.weights = [0.0] * d
        # Start at the base rate so the model does not have to learn the intercept.
        base = sum(y) / n
        base = min(max(base, 1e-6), 1 - 1e-6)
        self.bias = math.log(base / (1 - base))

        previous_loss = float("inf")
        for step in range(self.iterations):
            grad_w = [0.0] * d
            grad_b = 0.0
            loss = 0.0
            for row, label in zip(X, y):
                z = self.bias + sum(w * x for w, x in zip(self.weights, row))
                p = sigmoid(z)
                error = p - label
                grad_b += error
                for j in range(d):
                    grad_w[j] += error * row[j]
                p_clipped = min(max(p, 1e-12), 1 - 1e-12)
                loss -= label * math.log(p_clipped) + (1 - label) * math.log(1 - p_clipped)

            loss = loss / n + 0.5 * self.l2 * sum(w * w for w in self.weights) / n
            self.bias -= self.learning_rate * grad_b / n
            for j in range(d):
                self.weights[j] -= self.learning_rate * (grad_w[j] / n + self.l2 * self.weights[j] / n)

            self.n_iter_ = step + 1
            if abs(previous_loss - loss) < self.tolerance:
                break
            previous_loss = loss

        return self

    def decision_function(self, X: Sequence[Sequence[float]]) -> List[float]:
        return [self.bias + sum(w * x for w, x in zip(self.weights, row)) for row in X]

    def predict_proba(self, X: Sequence[Sequence[float]]) -> List[float]:
        """Probability that the next bar closes higher."""
        return [sigmoid(z) for z in self.decision_function(X)]

    def predict(self, X: Sequence[Sequence[float]], threshold: float = 0.5) -> List[int]:
        return [1 if p >= threshold else 0 for p in self.predict_proba(X)]


@dataclass
class Pipeline:
    """Standardizer + classifier, fit and applied as one unit."""

    l2: float = 1.0
    learning_rate: float = 0.2
    iterations: int = 400
    scaler: Standardizer = field(default_factory=Standardizer)
    classifier: LogisticRegression = field(default_factory=LogisticRegression)

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]) -> "Pipeline":
        self.classifier = LogisticRegression(
            l2=self.l2, learning_rate=self.learning_rate, iterations=self.iterations
        )
        self.scaler = Standardizer()
        self.classifier.fit(self.scaler.fit_transform(X), y)
        return self

    def predict_proba(self, X: Sequence[Sequence[float]]) -> List[float]:
        return self.classifier.predict_proba(self.scaler.transform(X))

    def coefficients(self, names: Sequence[str]) -> List[Tuple[str, float]]:
        """Standardized coefficients, largest absolute value first."""
        pairs = list(zip(names, self.classifier.weights))
        return sorted(pairs, key=lambda kv: abs(kv[1]), reverse=True)


def accuracy(predictions: Sequence[int], y: Sequence[int]) -> float:
    if not y:
        return 0.0
    return sum(1 for p, t in zip(predictions, y) if p == t) / len(y)


def log_loss(probabilities: Sequence[float], y: Sequence[int]) -> float:
    if not y:
        return 0.0
    total = 0.0
    for p, t in zip(probabilities, y):
        p = min(max(p, 1e-12), 1 - 1e-12)
        total -= t * math.log(p) + (1 - t) * math.log(1 - p)
    return total / len(y)

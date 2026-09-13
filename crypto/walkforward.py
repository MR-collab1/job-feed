"""Walk-forward (rolling-origin) evaluation.

A single train/test split on time-series data is close to worthless: you tune
until the one test period looks good, which is just slow overfitting. Here the
model is refit on each rolling window and only ever predicts bars that came
strictly after its training data. The concatenated test predictions are the
only results this package treats as real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .backtest import BacktestResult, CostModel, SizingConfig, run_backtest, size_positions
from .features import Dataset
from .model import Pipeline, accuracy, log_loss


@dataclass
class WalkForwardConfig:
    train_size: int = 500     # bars used to fit each model
    test_size: int = 50       # bars predicted before refitting
    step: Optional[int] = None  # defaults to test_size (non-overlapping folds)
    l2: float = 1.0
    learning_rate: float = 0.2
    iterations: int = 400
    embargo: int = 1          # bars skipped between train and test

    def resolved_step(self) -> int:
        return self.step if self.step is not None else self.test_size


@dataclass
class Fold:
    index: int
    train_start: int
    train_end: int      # exclusive
    test_start: int
    test_end: int       # exclusive
    train_accuracy: float
    test_accuracy: float
    test_log_loss: float
    top_features: List[Tuple[str, float]] = field(default_factory=list)


@dataclass
class WalkForwardResult:
    folds: List[Fold]
    probabilities: List[float]
    forward_returns: List[float]
    labels: List[int]
    timestamps: List[int]
    sample_indices: List[int]

    @property
    def out_of_sample_size(self) -> int:
        return len(self.probabilities)

    def directional_accuracy(self) -> float:
        predictions = [1 if p >= 0.5 else 0 for p in self.probabilities]
        return accuracy(predictions, self.labels)


def make_folds(n_samples: int, config: WalkForwardConfig) -> List[Tuple[int, int, int, int]]:
    """Yield (train_start, train_end, test_start, test_end) index tuples."""
    step = config.resolved_step()
    if config.train_size < 20:
        raise ValueError("train_size must be at least 20 samples")
    if config.test_size < 1 or step < 1:
        raise ValueError("test_size and step must be positive")

    folds: List[Tuple[int, int, int, int]] = []
    train_start = 0
    while True:
        train_end = train_start + config.train_size
        test_start = train_end + config.embargo
        test_end = min(test_start + config.test_size, n_samples)
        if test_start >= n_samples or test_end <= test_start:
            break
        folds.append((train_start, train_end, test_start, test_end))
        train_start += step
    return folds


def run_walk_forward(dataset: Dataset, config: WalkForwardConfig) -> WalkForwardResult:
    """Refit on each rolling window and collect strictly out-of-sample predictions."""
    n = len(dataset)
    fold_specs = make_folds(n, config)
    if not fold_specs:
        raise ValueError(
            f"{n} samples is too few for train_size={config.train_size} "
            f"and test_size={config.test_size}; fetch more history or shrink the windows"
        )

    folds: List[Fold] = []
    probabilities: List[float] = []
    forwards: List[float] = []
    labels: List[int] = []
    timestamps: List[int] = []
    indices: List[int] = []

    for number, (tr0, tr1, te0, te1) in enumerate(fold_specs):
        X_train = dataset.X[tr0:tr1]
        y_train = dataset.y[tr0:tr1]
        X_test = dataset.X[te0:te1]
        y_test = dataset.y[te0:te1]

        pipeline = Pipeline(
            l2=config.l2, learning_rate=config.learning_rate, iterations=config.iterations
        ).fit(X_train, y_train)

        train_probabilities = pipeline.predict_proba(X_train)
        test_probabilities = pipeline.predict_proba(X_test)

        folds.append(
            Fold(
                index=number,
                train_start=tr0,
                train_end=tr1,
                test_start=te0,
                test_end=te1,
                train_accuracy=accuracy([1 if p >= 0.5 else 0 for p in train_probabilities], y_train),
                test_accuracy=accuracy([1 if p >= 0.5 else 0 for p in test_probabilities], y_test),
                test_log_loss=log_loss(test_probabilities, y_test),
                top_features=pipeline.coefficients(dataset.names)[:5],
            )
        )

        probabilities.extend(test_probabilities)
        forwards.extend(dataset.forward_return[te0:te1])
        labels.extend(y_test)
        timestamps.extend(dataset.timestamp[te0:te1])
        indices.extend(range(te0, te1))

    return WalkForwardResult(
        folds=folds,
        probabilities=probabilities,
        forward_returns=forwards,
        labels=labels,
        timestamps=timestamps,
        sample_indices=indices,
    )


def backtest_walk_forward(
    result: WalkForwardResult,
    sizing: SizingConfig,
    costs: CostModel,
    periods_per_year: float,
    vol_estimate: Optional[Sequence[float]] = None,
) -> BacktestResult:
    positions = size_positions(result.probabilities, sizing, vol_estimate)
    return run_backtest(positions, result.forward_returns, periods_per_year, costs)

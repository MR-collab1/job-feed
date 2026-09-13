"""Is the result real, or did the search find noise?

Any strategy tested on a few hundred out-of-sample bars will show *some*
Sharpe ratio. These tools estimate how easily pure luck produces the same
number. Run them before believing anything.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import NormalDist
from typing import List, Optional, Sequence

from .backtest import CostModel, run_backtest
from .metrics import mean, sharpe_ratio, stdev

_NORMAL = NormalDist()
EULER_MASCHERONI = 0.5772156649015329


def skewness(xs: Sequence[float]) -> float:
    n = len(xs)
    sd = stdev(xs)
    if n < 3 or sd == 0:
        return 0.0
    mu = mean(xs)
    return sum(((x - mu) / sd) ** 3 for x in xs) / n


def kurtosis(xs: Sequence[float]) -> float:
    """Non-excess kurtosis; 3.0 for a normal distribution."""
    n = len(xs)
    sd = stdev(xs)
    if n < 4 or sd == 0:
        return 3.0
    mu = mean(xs)
    return sum(((x - mu) / sd) ** 4 for x in xs) / n


@dataclass
class PermutationResult:
    observed_sharpe: float
    null_mean: float
    null_std: float
    p_value: float
    trials: int
    percentile: float

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05


def permutation_test(
    positions: Sequence[float],
    forward_returns: Sequence[float],
    periods_per_year: float,
    costs: Optional[CostModel] = None,
    trials: int = 500,
    seed: int = 11,
) -> PermutationResult:
    """Shuffle *when* the positions were held, keeping the same position mix.

    The null hypothesis is that the model's sizing carries no timing
    information: the same set of positions, applied on randomly chosen bars,
    would do just as well. A p-value above 0.05 means the backtest is
    indistinguishable from that null, whatever the equity curve looks like.
    """
    cost_model = costs or CostModel()
    observed = run_backtest(positions, forward_returns, periods_per_year, cost_model)
    observed_sharpe = observed.performance.sharpe

    rng = random.Random(seed)
    shuffled = list(positions)
    null_sharpes: List[float] = []
    for _ in range(trials):
        rng.shuffle(shuffled)
        result = run_backtest(shuffled, forward_returns, periods_per_year, cost_model)
        null_sharpes.append(result.performance.sharpe)

    at_least_as_good = sum(1 for s in null_sharpes if s >= observed_sharpe)
    # Add-one smoothing keeps the p-value away from an impossible exact zero.
    p_value = (at_least_as_good + 1) / (trials + 1)
    below = sum(1 for s in null_sharpes if s < observed_sharpe)

    return PermutationResult(
        observed_sharpe=observed_sharpe,
        null_mean=mean(null_sharpes),
        null_std=stdev(null_sharpes),
        p_value=p_value,
        trials=trials,
        percentile=100.0 * below / max(1, trials),
    )


def block_bootstrap_sharpe(
    returns: Sequence[float],
    periods_per_year: float,
    trials: int = 1000,
    block_size: int = 10,
    seed: int = 13,
) -> tuple[float, float]:
    """5th and 95th percentile of the Sharpe ratio under a moving-block bootstrap.

    Blocks preserve short-horizon autocorrelation, which independent
    resampling destroys and which flatters the confidence interval.
    """
    n = len(returns)
    if n < block_size * 2:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n_blocks = math.ceil(n / block_size)
    samples: List[float] = []
    for _ in range(trials):
        resampled: List[float] = []
        for _ in range(n_blocks):
            start = rng.randrange(0, n - block_size + 1)
            resampled.extend(returns[start : start + block_size])
        samples.append(sharpe_ratio(resampled[:n], periods_per_year))
    samples.sort()
    low = samples[int(0.05 * (trials - 1))]
    high = samples[int(0.95 * (trials - 1))]
    return (low, high)


def probabilistic_sharpe_ratio(
    returns: Sequence[float], periods_per_year: float, benchmark_sharpe: float = 0.0
) -> float:
    """Probability the true Sharpe exceeds `benchmark_sharpe`.

    Adjusts for sample length, skew and fat tails, all of which make a naive
    Sharpe ratio look more trustworthy than it is on crypto returns.
    """
    n = len(returns)
    if n < 3:
        return 0.0
    sharpe_annual = sharpe_ratio(returns, periods_per_year)
    # Work in per-period units, where the standard error formula applies.
    scale = math.sqrt(periods_per_year)
    sr = sharpe_annual / scale
    benchmark = benchmark_sharpe / scale
    g3 = skewness(returns)
    g4 = kurtosis(returns)
    denominator = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr * sr
    if denominator <= 0:
        return 0.0
    z = (sr - benchmark) * math.sqrt(n - 1) / math.sqrt(denominator)
    return _NORMAL.cdf(z)


def expected_max_sharpe_under_null(n_trials: int, trial_sharpe_std: float) -> float:
    """Highest Sharpe you should expect from `n_trials` worthless strategies.

    Backtest a hundred variants of a coin flip and the best one looks good.
    This is the bar that the best of your search must clear before it means
    anything, and it is the reason to count every configuration you tried.
    """
    if n_trials <= 1 or trial_sharpe_std <= 0:
        return 0.0
    a = _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    b = _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return trial_sharpe_std * ((1.0 - EULER_MASCHERONI) * a + EULER_MASCHERONI * b)


def sharpe_standard_error(n_observations: int, periods_per_year: float) -> float:
    """Rough annualized standard error of a Sharpe estimate under the null."""
    if n_observations < 2:
        return float("inf")
    return math.sqrt(periods_per_year / n_observations)

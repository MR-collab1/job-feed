"""Causal feature engineering.

Every feature at index i is computed only from bars[0..i] inclusive. The label
is the direction of the move from close[i] to close[i+1]. That alignment is
the single most important thing in this package: get it wrong and a backtest
reports spectacular, entirely fictional returns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .data import Bar

WARMUP = 50  # longest lookback used by any feature below

FEATURE_NAMES: Tuple[str, ...] = (
    "ret_1",
    "ret_5",
    "ret_10",
    "ret_20",
    "mom_accel",
    "vol_20",
    "vol_ratio",
    "rsi_14",
    "sma_ratio_20",
    "sma_ratio_50",
    "range_5",
    "volume_z",
    "close_position",
    "dist_high_20",
    "dist_low_20",
)


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0 or not math.isfinite(denominator):
        return default
    value = numerator / denominator
    return value if math.isfinite(value) else default


def _log_return(closes: Sequence[float], i: int, lag: int) -> float:
    prior = closes[i - lag]
    if prior <= 0 or closes[i] <= 0:
        return 0.0
    return math.log(closes[i] / prior)


def _stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mu = sum(xs) / n
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))


def _rsi(closes: Sequence[float], i: int, window: int = 14) -> float:
    """Wilder-style RSI rescaled to [-1, 1]; 0.0 means neutral."""
    gains = 0.0
    losses = 0.0
    for k in range(i - window + 1, i + 1):
        change = closes[k] - closes[k - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    if gains + losses == 0:
        return 0.0
    rsi = 100.0 * gains / (gains + losses)
    return (rsi - 50.0) / 50.0


def feature_row(bars: Sequence[Bar], i: int) -> List[float]:
    """Feature vector observable at the close of bar i."""
    if i < WARMUP:
        raise ValueError(f"index {i} is inside the {WARMUP}-bar warmup window")

    closes = [b.close for b in bars]
    rets = [_log_return(closes, k, 1) for k in range(i - 20, i + 1)]

    ret_1 = _log_return(closes, i, 1)
    ret_5 = _log_return(closes, i, 5)
    ret_10 = _log_return(closes, i, 10)
    ret_20 = _log_return(closes, i, 20)
    mom_accel = ret_5 / 5.0 - ret_20 / 20.0

    vol_20 = _stdev(rets[-20:])
    vol_5 = _stdev(rets[-5:])
    vol_ratio = _safe_div(vol_5, vol_20, 1.0) - 1.0

    sma_20 = sum(closes[i - 19 : i + 1]) / 20.0
    sma_50 = sum(closes[i - 49 : i + 1]) / 50.0
    sma_ratio_20 = _safe_div(closes[i], sma_20, 1.0) - 1.0
    sma_ratio_50 = _safe_div(closes[i], sma_50, 1.0) - 1.0

    range_5 = sum(
        _safe_div(bars[k].high - bars[k].low, bars[k].close) for k in range(i - 4, i + 1)
    ) / 5.0

    volumes = [bars[k].volume for k in range(i - 19, i + 1)]
    vol_mean = sum(volumes) / len(volumes)
    volume_z = _safe_div(volumes[-1] - vol_mean, _stdev(volumes))

    span = bars[i].high - bars[i].low
    close_position = 2.0 * _safe_div(bars[i].close - bars[i].low, span, 0.5) - 1.0

    high_20 = max(bars[k].high for k in range(i - 19, i + 1))
    low_20 = min(bars[k].low for k in range(i - 19, i + 1))
    dist_high_20 = _safe_div(closes[i], high_20, 1.0) - 1.0
    dist_low_20 = _safe_div(closes[i], low_20, 1.0) - 1.0

    row = [
        ret_1,
        ret_5,
        ret_10,
        ret_20,
        mom_accel,
        vol_20,
        vol_ratio,
        _rsi(closes, i),
        sma_ratio_20,
        sma_ratio_50,
        range_5,
        volume_z,
        close_position,
        dist_high_20,
        dist_low_20,
    ]
    return [v if math.isfinite(v) else 0.0 for v in row]


@dataclass
class Dataset:
    """Aligned features, labels and forward returns.

    For sample k: `X[k]` is known at the close of bar `bar_index[k]`, and
    `forward_return[k]` is the simple return earned by holding from that close
    to the next close. `y[k]` is 1 when that forward return is positive.
    """

    X: List[List[float]]
    y: List[int]
    forward_return: List[float]
    timestamp: List[int]
    bar_index: List[int]
    names: Tuple[str, ...] = FEATURE_NAMES

    def __len__(self) -> int:
        return len(self.X)


def build_dataset(bars: Sequence[Bar]) -> Dataset:
    """Build the supervised dataset, dropping the final bar (no future to learn)."""
    if len(bars) < WARMUP + 2:
        raise ValueError(f"need at least {WARMUP + 2} bars, got {len(bars)}")

    X: List[List[float]] = []
    y: List[int] = []
    fwd: List[float] = []
    ts: List[int] = []
    idx: List[int] = []

    for i in range(WARMUP, len(bars) - 1):
        forward = bars[i + 1].close / bars[i].close - 1.0
        X.append(feature_row(bars, i))
        y.append(1 if forward > 0 else 0)
        fwd.append(forward)
        ts.append(bars[i].ts)
        idx.append(i)

    return Dataset(X=X, y=y, forward_return=fwd, timestamp=ts, bar_index=idx)

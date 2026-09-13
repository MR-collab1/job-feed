"""Performance metrics for a series of periodic returns."""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Sequence


def equity_curve(returns: Sequence[float], initial: float = 1.0) -> List[float]:
    """Compound a series of simple period returns into an equity curve.

    The returned list has len(returns) + 1 points; the first is `initial`.
    """
    curve = [float(initial)]
    equity = float(initial)
    for r in returns:
        equity *= 1.0 + r
        if equity < 0.0:
            equity = 0.0
        curve.append(equity)
    return curve


def total_return(returns: Sequence[float]) -> float:
    curve = equity_curve(returns)
    return curve[-1] / curve[0] - 1.0


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs: Sequence[float], ddof: int = 1) -> float:
    n = len(xs)
    if n - ddof <= 0:
        return 0.0
    mu = mean(xs)
    var = sum((x - mu) ** 2 for x in xs) / (n - ddof)
    return math.sqrt(var)


def annualized_return(returns: Sequence[float], periods_per_year: float) -> float:
    """Geometric annualized return implied by the realized period returns."""
    if not returns:
        return 0.0
    growth = 1.0
    for r in returns:
        growth *= 1.0 + r
    if growth <= 0.0:
        return -1.0
    years = len(returns) / periods_per_year
    if years <= 0:
        return 0.0
    return growth ** (1.0 / years) - 1.0


def annualized_volatility(returns: Sequence[float], periods_per_year: float) -> float:
    return stdev(returns) * math.sqrt(periods_per_year)


def sharpe_ratio(
    returns: Sequence[float], periods_per_year: float, risk_free_annual: float = 0.0
) -> float:
    """Annualized Sharpe ratio of the period returns.

    Returns 0.0 when volatility is zero, rather than raising, so that a
    do-nothing strategy scores neutrally instead of blowing up the report.
    """
    if len(returns) < 2:
        return 0.0
    sd = stdev(returns)
    if sd == 0.0:
        return 0.0
    rf_per_period = risk_free_annual / periods_per_year
    excess = [r - rf_per_period for r in returns]
    return mean(excess) / sd * math.sqrt(periods_per_year)


def sortino_ratio(
    returns: Sequence[float], periods_per_year: float, risk_free_annual: float = 0.0
) -> float:
    if len(returns) < 2:
        return 0.0
    rf_per_period = risk_free_annual / periods_per_year
    excess = [r - rf_per_period for r in returns]
    downside = [min(0.0, r) for r in excess]
    dd = math.sqrt(sum(d * d for d in downside) / len(downside))
    if dd == 0.0:
        return 0.0
    return mean(excess) / dd * math.sqrt(periods_per_year)


def max_drawdown(returns: Sequence[float]) -> float:
    """Worst peak-to-trough decline of the equity curve, as a negative fraction."""
    curve = equity_curve(returns)
    peak = curve[0]
    worst = 0.0
    for value in curve:
        if value > peak:
            peak = value
        if peak > 0:
            dd = value / peak - 1.0
            if dd < worst:
                worst = dd
    return worst


def hit_rate(pnl: Sequence[float]) -> float:
    """Fraction of non-zero periods that were profitable."""
    active = [p for p in pnl if p != 0.0]
    if not active:
        return 0.0
    return sum(1 for p in active if p > 0) / len(active)


def calmar_ratio(returns: Sequence[float], periods_per_year: float) -> float:
    mdd = max_drawdown(returns)
    if mdd == 0.0:
        return 0.0
    return annualized_return(returns, periods_per_year) / abs(mdd)


@dataclass
class Performance:
    periods: int
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    calmar: float
    hit_rate: float

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)


def summarize(returns: Sequence[float], periods_per_year: float) -> Performance:
    return Performance(
        periods=len(returns),
        total_return=total_return(returns),
        annualized_return=annualized_return(returns, periods_per_year),
        annualized_volatility=annualized_volatility(returns, periods_per_year),
        sharpe=sharpe_ratio(returns, periods_per_year),
        sortino=sortino_ratio(returns, periods_per_year),
        max_drawdown=max_drawdown(returns),
        calmar=calmar_ratio(returns, periods_per_year),
        hit_rate=hit_rate(returns),
    )

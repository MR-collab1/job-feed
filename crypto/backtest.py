"""Position sizing and a cost-aware backtest.

Costs are not a detail. A daily signal that flips position every other bar
pays roughly 100 round trips a year; at 10 bps per side that is ~2% of capital
annually before slippage, which is larger than the edge most retail signals
actually have. This module makes those costs impossible to ignore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .metrics import Performance, equity_curve, summarize


@dataclass
class CostModel:
    """All costs in basis points (1 bp = 0.01%)."""

    fee_bps: float = 10.0            # taker fee per side, typical retail spot tier
    slippage_bps: float = 5.0        # market impact + spread crossing per side
    borrow_bps_annual: float = 500.0  # financing on the short leg, if shorting

    def trade_cost(self, position_change: float) -> float:
        return abs(position_change) * (self.fee_bps + self.slippage_bps) / 10_000.0

    def financing_cost(self, position: float, periods_per_year: float) -> float:
        if position >= 0 or periods_per_year <= 0:
            return 0.0
        return abs(position) * (self.borrow_bps_annual / 10_000.0) / periods_per_year


@dataclass
class SizingConfig:
    """How a probability becomes a position, expressed in units of capital."""

    mode: str = "threshold"       # "threshold" or "proportional"
    band: float = 0.02            # only act when |p - 0.5| exceeds this
    allow_short: bool = False     # spot-only accounts cannot short
    max_position: float = 1.0     # 1.0 = fully invested, no leverage
    target_vol_annual: Optional[float] = None  # optional volatility targeting


def size_positions(
    probabilities: Sequence[float],
    config: SizingConfig,
    vol_estimate: Optional[Sequence[float]] = None,
) -> List[float]:
    """Map predicted up-probabilities to positions in [-max, +max]."""
    positions: List[float] = []
    for k, p in enumerate(probabilities):
        edge = p - 0.5
        if abs(edge) <= config.band:
            raw = 0.0
        elif config.mode == "threshold":
            raw = 1.0 if edge > 0 else -1.0
        elif config.mode == "proportional":
            # Scale so that an edge of 4x the band is a full-size position.
            denominator = max(config.band * 4.0, 1e-9)
            raw = max(-1.0, min(1.0, edge / denominator))
        else:
            raise ValueError(f"unknown sizing mode {config.mode!r}")

        if raw < 0 and not config.allow_short:
            raw = 0.0

        position = raw * config.max_position

        if config.target_vol_annual is not None and vol_estimate is not None:
            realized = vol_estimate[k] if k < len(vol_estimate) else 0.0
            if realized and realized > 1e-9:
                scale = config.target_vol_annual / realized
                position *= max(0.0, min(3.0, scale))
                position = max(-config.max_position, min(config.max_position, position))

        positions.append(position)
    return positions


@dataclass
class BacktestResult:
    positions: List[float]
    gross_returns: List[float]
    costs: List[float]
    net_returns: List[float]
    equity: List[float]
    turnover: float
    round_trips: float
    exposure: float
    performance: Performance = field(init=False)
    periods_per_year: float = 365.25

    def __post_init__(self) -> None:
        self.performance = summarize(self.net_returns, self.periods_per_year)

    @property
    def total_cost(self) -> float:
        return sum(self.costs)


def run_backtest(
    positions: Sequence[float],
    forward_returns: Sequence[float],
    periods_per_year: float,
    costs: Optional[CostModel] = None,
    initial_position: float = 0.0,
) -> BacktestResult:
    """Apply positions to forward returns and subtract trading costs.

    `positions[k]` is decided at the close of bar k using only information
    available then; `forward_returns[k]` is what holding it earns over the
    following bar. Costs are charged when the position changes.
    """
    if len(positions) != len(forward_returns):
        raise ValueError("positions and forward_returns must be the same length")
    cost_model = costs or CostModel()

    gross: List[float] = []
    charged: List[float] = []
    net: List[float] = []
    traded_notional = 0.0
    previous = initial_position

    for position, forward in zip(positions, forward_returns):
        trade = cost_model.trade_cost(position - previous)
        financing = cost_model.financing_cost(position, periods_per_year)
        gross_return = position * forward
        total_cost = trade + financing

        gross.append(gross_return)
        charged.append(total_cost)
        net.append(gross_return - total_cost)
        traded_notional += abs(position - previous)
        previous = position

    n = max(1, len(positions))
    return BacktestResult(
        positions=list(positions),
        gross_returns=gross,
        costs=charged,
        net_returns=net,
        equity=equity_curve(net),
        turnover=traded_notional / n * periods_per_year,
        round_trips=traded_notional / 2.0,
        exposure=sum(abs(p) for p in positions) / n,
        periods_per_year=periods_per_year,
    )


def buy_and_hold(forward_returns: Sequence[float], periods_per_year: float,
                 costs: Optional[CostModel] = None) -> BacktestResult:
    """The benchmark that actually matters: hold the asset, pay one entry fee."""
    positions = [1.0] * len(forward_returns)
    return run_backtest(positions, forward_returns, periods_per_year, costs)


def break_even_accuracy(costs: CostModel, average_absolute_return: float,
                        trades_per_period: float = 1.0) -> float:
    """Directional accuracy needed just to cover costs.

    With symmetric payoffs, a hit rate h earns (2h - 1) * |r| per period and
    pays `trades_per_period` round trips of cost. This returns the h at which
    those two are equal, which is the bar any signal has to clear.
    """
    if average_absolute_return <= 0:
        return 1.0
    cost_per_period = trades_per_period * costs.trade_cost(2.0)
    h = 0.5 + cost_per_period / (2.0 * average_absolute_return)
    return min(1.0, h)

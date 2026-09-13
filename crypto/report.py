"""Human-readable research report, written to be hard to fool yourself with."""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Sequence

from .backtest import BacktestResult, CostModel, break_even_accuracy
from .metrics import Performance, mean
from .significance import PermutationResult
from .walkforward import WalkForwardConfig, WalkForwardResult

LINE = "=" * 72
THIN = "-" * 72


def _pct(x: float) -> str:
    return f"{x * 100:>8.2f}%"


def _num(x: float) -> str:
    return f"{x:>9.3f}"


def _date(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def performance_table(rows: Sequence[tuple[str, Performance]]) -> str:
    header = (
        f"{'':<22}{'total':>10}{'ann.ret':>10}{'ann.vol':>10}"
        f"{'Sharpe':>10}{'maxDD':>10}{'hit':>8}"
    )
    lines = [header, THIN]
    for name, p in rows:
        lines.append(
            f"{name:<22}{p.total_return * 100:>9.1f}%{p.annualized_return * 100:>9.1f}%"
            f"{p.annualized_volatility * 100:>9.1f}%{p.sharpe:>10.2f}"
            f"{p.max_drawdown * 100:>9.1f}%{p.hit_rate * 100:>7.1f}%"
        )
    return "\n".join(lines)


def build_report(
    *,
    symbol: str,
    source: str,
    interval: str,
    n_bars: int,
    first_ts: int,
    last_ts: int,
    periods_per_year: float,
    costs: CostModel,
    wf_config: WalkForwardConfig,
    wf_result: WalkForwardResult,
    strategy: BacktestResult,
    benchmark: BacktestResult,
    permutation: Optional[PermutationResult],
    bootstrap_ci: Optional[tuple[float, float]],
    psr: Optional[float],
    expected_max_null_sharpe: Optional[float] = None,
) -> str:
    average_move = mean([abs(r) for r in wf_result.forward_returns])
    trades_per_period = strategy.turnover / max(periods_per_year, 1e-9) / 2.0
    breakeven = break_even_accuracy(costs, average_move, max(trades_per_period, 1e-9))
    oos_accuracy = wf_result.directional_accuracy()

    train_acc = mean([f.train_accuracy for f in wf_result.folds])
    test_acc = mean([f.test_accuracy for f in wf_result.folds])

    out: List[str] = []
    out.append(LINE)
    out.append(f"CRYPTO STRATEGY RESEARCH REPORT — {symbol} ({interval}, source={source})")
    out.append(LINE)
    out.append("")
    out.append("DATA")
    out.append(THIN)
    out.append(f"  bars                {n_bars}")
    out.append(f"  period              {_date(first_ts)} to {_date(last_ts)}")
    out.append(f"  bars per year       {periods_per_year:.1f}")
    out.append("")

    out.append("COST ASSUMPTIONS  (change these and the conclusion changes)")
    out.append(THIN)
    out.append(f"  fee                 {costs.fee_bps:.1f} bps per side")
    out.append(f"  slippage            {costs.slippage_bps:.1f} bps per side")
    out.append(f"  short financing     {costs.borrow_bps_annual:.0f} bps per year")
    out.append(f"  average bar move    {average_move * 100:.2f}%")
    out.append(f"  round trips / year  {strategy.turnover / 2:.1f}")
    out.append(f"  BREAK-EVEN ACCURACY {breakeven * 100:.2f}%  <- the bar to clear")
    out.append("")

    out.append("WALK-FORWARD SETUP")
    out.append(THIN)
    out.append(f"  train window        {wf_config.train_size} bars")
    out.append(f"  test window         {wf_config.test_size} bars")
    out.append(f"  embargo             {wf_config.embargo} bar(s)")
    out.append(f"  folds               {len(wf_result.folds)}")
    out.append(f"  out-of-sample bars  {wf_result.out_of_sample_size}")
    out.append("")

    out.append("CLASSIFICATION (out of sample)")
    out.append(THIN)
    out.append(f"  in-sample accuracy  {train_acc * 100:.2f}%")
    out.append(f"  out-of-sample acc.  {test_acc * 100:.2f}%")
    out.append(f"  pooled OOS accuracy {oos_accuracy * 100:.2f}%")
    out.append(f"  overfit gap         {(train_acc - test_acc) * 100:+.2f} pts")
    out.append("")

    out.append("PERFORMANCE (out of sample only)")
    out.append(THIN)
    out.append(
        performance_table(
            [
                ("strategy (net)", strategy.performance),
                ("buy & hold", benchmark.performance),
            ]
        )
    )
    gross_drag = sum(strategy.gross_returns) - sum(strategy.net_returns)
    out.append("")
    out.append(f"  cost drag           {gross_drag * 100:.2f}% of capital over the test span")
    out.append(f"  average exposure    {strategy.exposure * 100:.1f}% of capital")
    out.append("")

    out.append("STATISTICAL SIGNIFICANCE")
    out.append(THIN)
    if permutation is not None:
        out.append(f"  permutation p-value {permutation.p_value:.4f}  ({permutation.trials} shuffles)")
        out.append(
            f"  null Sharpe         mean {permutation.null_mean:.2f}, "
            f"sd {permutation.null_std:.2f}; observed sits at the "
            f"{permutation.percentile:.1f}th percentile"
        )
    if bootstrap_ci is not None:
        out.append(f"  Sharpe 90% CI       [{bootstrap_ci[0]:.2f}, {bootstrap_ci[1]:.2f}]")
    if psr is not None:
        out.append(f"  P(true Sharpe > 0)  {psr * 100:.1f}%")
    if expected_max_null_sharpe:
        out.append(
            f"  luck benchmark      a search over worthless variants would produce "
            f"a best Sharpe near {expected_max_null_sharpe:.2f}"
        )
    out.append("")

    out.append("VERDICT")
    out.append(THIN)
    checks: Dict[str, bool] = {
        "net Sharpe is positive": strategy.performance.sharpe > 0,
        "beats buy & hold on Sharpe": strategy.performance.sharpe > benchmark.performance.sharpe,
        "clears break-even accuracy": oos_accuracy > breakeven,
        "permutation p < 0.05": bool(permutation and permutation.p_value < 0.05),
        "P(true Sharpe > 0) > 95%": bool(psr and psr > 0.95),
        "bootstrap CI excludes zero": bool(bootstrap_ci and bootstrap_ci[0] > 0),
    }
    for label, passed in checks.items():
        out.append(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    out.append("")

    if all(checks.values()):
        out.append("  All checks pass on this one configuration. That is necessary, not")
        out.append("  sufficient. Before risking money: re-run on other symbols and other")
        out.append("  date ranges, count every variant you tried against the luck benchmark")
        out.append("  above, then paper trade live for months. Backtests do not slip, do not")
        out.append("  get rate-limited, and never miss a fill.")
    else:
        failed = [name for name, ok in checks.items() if not ok]
        out.append(f"  {len(failed)} check(s) failed: {', '.join(failed)}.")
        out.append("  No demonstrated edge. Trading this configuration is a paid education.")
        out.append("  This is the ordinary result, and finding it here costs nothing.")
    out.append("")
    out.append(LINE)
    out.append("Research output. Not financial advice. Past performance in a backtest")
    out.append("is not evidence of future returns.")
    out.append(LINE)
    return "\n".join(out)


def fold_detail(wf_result: WalkForwardResult) -> str:
    lines = [f"{'fold':>5}{'train acc':>12}{'test acc':>11}{'log loss':>11}   top features"]
    lines.append(THIN)
    for fold in wf_result.folds:
        top = ", ".join(f"{n}{'+' if c > 0 else '-'}" for n, c in fold.top_features[:3])
        lines.append(
            f"{fold.index:>5}{fold.train_accuracy * 100:>11.1f}%"
            f"{fold.test_accuracy * 100:>10.1f}%{fold.test_log_loss:>11.4f}   {top}"
        )
    return "\n".join(lines)

"""Command line entry point:  python3 -m crypto ..."""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import List, Optional, Sequence

from . import data as data_module
from .backtest import CostModel, SizingConfig, buy_and_hold, size_positions
from .features import FEATURE_NAMES, build_dataset
from .model import Pipeline
from .report import build_report, fold_detail
from .significance import (
    block_bootstrap_sharpe,
    expected_max_sharpe_under_null,
    permutation_test,
    probabilistic_sharpe_ratio,
    sharpe_standard_error,
)
from .walkforward import WalkForwardConfig, backtest_walk_forward, run_walk_forward

BANNER = """\
This tool does research only. It never connects to a wallet or an exchange
account, holds no keys, and cannot place an order. Any position it prints is
a paper trade for you to evaluate, not an instruction.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m crypto",
        description="Backtest and stress-test a systematic crypto strategy.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_argument_group("data")
    source.add_argument("--source", default="coinbase",
                        choices=["coinbase", "binance", "csv", "synthetic"])
    source.add_argument("--symbol", default="BTC-USD")
    source.add_argument("--interval", default="1d",
                        choices=["1m", "5m", "15m", "1h", "6h", "1d"])
    source.add_argument("--bars", type=int, default=1200, help="how much history to load")
    source.add_argument("--csv", dest="csv_path", default=None, help="path for --source csv")
    source.add_argument("--save-csv", default=None, help="cache the loaded candles here")

    windows = parser.add_argument_group("walk-forward")
    windows.add_argument("--train", type=int, default=500, help="training bars per fold")
    windows.add_argument("--test", type=int, default=50, help="test bars per fold")
    windows.add_argument("--embargo", type=int, default=1, help="bars skipped after training")
    windows.add_argument("--l2", type=float, default=1.0, help="L2 regularization strength")
    windows.add_argument("--iterations", type=int, default=400)

    trading = parser.add_argument_group("trading rules")
    trading.add_argument("--mode", default="threshold", choices=["threshold", "proportional"])
    trading.add_argument("--band", type=float, default=0.02,
                         help="minimum |p-0.5| before taking a position")
    trading.add_argument("--allow-short", action="store_true",
                         help="permit short positions (spot accounts cannot)")
    trading.add_argument("--max-position", type=float, default=1.0,
                         help="1.0 = fully invested, no leverage")
    trading.add_argument("--target-vol", type=float, default=None,
                         help="annualized volatility target, e.g. 0.4 for 40%%")

    cost = parser.add_argument_group("costs")
    cost.add_argument("--fee-bps", type=float, default=10.0)
    cost.add_argument("--slippage-bps", type=float, default=5.0)
    cost.add_argument("--borrow-bps", type=float, default=500.0)

    stats = parser.add_argument_group("significance")
    stats.add_argument("--permutations", type=int, default=500,
                       help="shuffles for the permutation test; 0 to skip")
    stats.add_argument("--bootstrap", type=int, default=500,
                       help="resamples for the Sharpe CI; 0 to skip")
    stats.add_argument("--trials-run", type=int, default=1,
                       help="how many configurations you have tried in total; "
                            "used to compute the luck benchmark")

    output = parser.add_argument_group("output")
    output.add_argument("--folds", action="store_true", help="print per-fold detail")
    output.add_argument("--json", dest="json_path", default=None, help="write metrics as JSON")
    output.add_argument("--equity-csv", default=None, help="write the OOS equity curve")
    output.add_argument("--signal", action="store_true",
                        help="also print today's paper-trade position")
    output.add_argument("--seed", type=int, default=7, help="seed for synthetic data")
    output.add_argument("--quiet", action="store_true", help="suppress the banner")
    return parser


def latest_signal(dataset, config: WalkForwardConfig, sizing: SizingConfig) -> dict:
    """Fit on the most recent training window and size the next position."""
    train_X = dataset.X[-config.train_size :]
    train_y = dataset.y[-config.train_size :]
    pipeline = Pipeline(l2=config.l2, iterations=config.iterations).fit(train_X, train_y)
    probability = pipeline.predict_proba([dataset.X[-1]])[0]
    position = size_positions([probability], sizing)[0]
    return {
        "as_of": dataset.timestamp[-1],
        "probability_up": probability,
        "paper_position": position,
        "top_features": pipeline.coefficients(FEATURE_NAMES)[:5],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.quiet:
        print(BANNER)

    try:
        bars = data_module.load_bars(
            source=args.source,
            symbol=args.symbol,
            interval=args.interval,
            bars=args.bars,
            csv_path=args.csv_path,
            seed=args.seed,
        )
    except data_module.DataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("hint: retry with --source synthetic to run fully offline.", file=sys.stderr)
        return 2

    if len(bars) < args.train + args.test + 60:
        print(
            f"error: only {len(bars)} bars available; need about "
            f"{args.train + args.test + 60} for train={args.train}, test={args.test}.",
            file=sys.stderr,
        )
        return 2

    if args.save_csv:
        data_module.save_csv(bars, args.save_csv)
        print(f"cached {len(bars)} bars to {args.save_csv}\n")

    periods = data_module.periods_per_year(bars)
    dataset = build_dataset(bars)

    wf_config = WalkForwardConfig(
        train_size=args.train,
        test_size=args.test,
        embargo=args.embargo,
        l2=args.l2,
        iterations=args.iterations,
    )
    wf_result = run_walk_forward(dataset, wf_config)

    sizing = SizingConfig(
        mode=args.mode,
        band=args.band,
        allow_short=args.allow_short,
        max_position=args.max_position,
        target_vol_annual=args.target_vol,
    )
    costs = CostModel(
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        borrow_bps_annual=args.borrow_bps,
    )

    vol_estimate: Optional[List[float]] = None
    if args.target_vol is not None:
        column = FEATURE_NAMES.index("vol_20")
        vol_estimate = [
            dataset.X[k][column] * math.sqrt(periods) for k in wf_result.sample_indices
        ]

    strategy = backtest_walk_forward(wf_result, sizing, costs, periods, vol_estimate)
    benchmark = buy_and_hold(wf_result.forward_returns, periods, costs)

    permutation = None
    if args.permutations > 0:
        permutation = permutation_test(
            strategy.positions, wf_result.forward_returns, periods, costs, args.permutations
        )
    bootstrap_ci = None
    if args.bootstrap > 0:
        bootstrap_ci = block_bootstrap_sharpe(strategy.net_returns, periods, args.bootstrap)
    psr = probabilistic_sharpe_ratio(strategy.net_returns, periods)
    luck = expected_max_sharpe_under_null(
        args.trials_run, sharpe_standard_error(len(strategy.net_returns), periods)
    )

    report = build_report(
        symbol=args.symbol,
        source=args.source,
        interval=args.interval,
        n_bars=len(bars),
        first_ts=bars[0].ts,
        last_ts=bars[-1].ts,
        periods_per_year=periods,
        costs=costs,
        wf_config=wf_config,
        wf_result=wf_result,
        strategy=strategy,
        benchmark=benchmark,
        permutation=permutation,
        bootstrap_ci=bootstrap_ci,
        psr=psr,
        expected_max_null_sharpe=luck,
    )
    print(report)

    if args.folds:
        print()
        print(fold_detail(wf_result))

    if args.signal:
        signal = latest_signal(dataset, wf_config, sizing)
        print()
        print("PAPER-TRADE SIGNAL (not an order, not advice)")
        print("-" * 72)
        print(f"  as of bar          {signal['as_of']}")
        print(f"  P(next bar up)     {signal['probability_up']:.4f}")
        print(f"  paper position     {signal['paper_position']:+.2f} of capital")
        print("  drivers            " + ", ".join(f"{n} {c:+.2f}" for n, c in signal["top_features"]))

    if args.equity_csv:
        with open(args.equity_csv, "w", encoding="utf-8") as handle:
            handle.write("timestamp,position,forward_return,net_return,equity\n")
            for k, ts in enumerate(wf_result.timestamps):
                handle.write(
                    f"{ts},{strategy.positions[k]:.6f},{wf_result.forward_returns[k]:.8f},"
                    f"{strategy.net_returns[k]:.8f},{strategy.equity[k + 1]:.8f}\n"
                )
        print(f"\nwrote equity curve to {args.equity_csv}")

    if args.json_path:
        payload = {
            "symbol": args.symbol,
            "source": args.source,
            "interval": args.interval,
            "bars": len(bars),
            "out_of_sample_bars": wf_result.out_of_sample_size,
            "folds": len(wf_result.folds),
            "oos_accuracy": wf_result.directional_accuracy(),
            "strategy": strategy.performance.as_dict(),
            "buy_and_hold": benchmark.performance.as_dict(),
            "turnover_per_year": strategy.turnover,
            "total_cost": strategy.total_cost,
            "permutation_p_value": permutation.p_value if permutation else None,
            "sharpe_ci_90": list(bootstrap_ci) if bootstrap_ci else None,
            "probabilistic_sharpe": psr,
            "expected_max_sharpe_under_null": luck,
        }
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"wrote metrics to {args.json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

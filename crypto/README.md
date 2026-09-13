# Crypto strategy research harness

A dependency-free Python toolkit for building a systematic crypto trading
model and, more importantly, for finding out whether it actually works.

```bash
python3 -m crypto --source synthetic --bars 1200        # runs offline, no network
python3 -m crypto --source coinbase --symbol BTC-USD --interval 1d --bars 1200
python3 -m crypto --source csv --csv my_data.csv --folds --signal
python3 -m unittest discover -s tests -t .              # 96 tests, ~6 seconds
```

Standard library only. No `pip install`. Python 3.9+.

## Read this before anything else

**This will most likely not make you money, and the tool is built to tell you
that.** Roughly:

- Predicting the direction of the next bar is close to a coin flip. A model
  that gets 52% right is doing well.
- At a 10 bps fee and 5 bps of slippage, every round trip costs 30 bps.
  Trading daily, that is about 1.1% of your capital per month in costs alone.
- The report prints a **break-even accuracy** figure. That is the hit rate you
  need just to pay costs. Compare it to the accuracy you actually achieved
  before getting excited about an equity curve.
- Buy and hold is the benchmark. Beating a coin flip is not enough; you have
  to beat simply owning the asset, after costs, on risk-adjusted terms.

The honest expected outcome of running this on Bitcoin daily bars is a report
that says **no demonstrated edge**. That is the useful result. It cost you a
few seconds of CPU instead of a year of tuition.

## What it will not do

There is no exchange integration, no wallet connection, no key handling, and
no order placement anywhere in this package, by design. It reads public price
history and prints numbers. Any position it reports is a paper trade for you
to evaluate.

Never paste a seed phrase or private key into any tool, script, or chat
window, including this one. Anyone with those twelve words owns everything in
the wallet, permanently and irreversibly. No legitimate software ever needs
them.

## How it works

| Module | Responsibility |
| --- | --- |
| `data.py` | OHLCV from CSV, public Coinbase/Binance endpoints, or a synthetic generator |
| `features.py` | 15 causal features and the aligned supervised dataset |
| `model.py` | Standardizer plus L2 logistic regression, fit with gradient descent |
| `backtest.py` | Position sizing and a fee, slippage and financing model |
| `walkforward.py` | Rolling-origin refitting so every prediction is out of sample |
| `significance.py` | Permutation test, block bootstrap, probabilistic Sharpe |
| `report.py` | The report, including a pass/fail verdict |
| `cli.py` | Argument parsing and orchestration |

### The three things that make a backtest honest

**1. No lookahead.** Every feature at bar `i` uses only bars up to and
including `i`. The label is the direction from close `i` to close `i+1`. This
is the single easiest thing to get wrong and the reason a broken backtest can
show a Sharpe ratio of 8. Two tests specifically try to break it: one
recomputes features on truncated history, another multiplies tomorrow's price
by 100 and asserts today's features do not move.

**2. Walk-forward evaluation.** The model is refit on a rolling window and
only ever predicts bars that came after its training data, with an embargo
between the two. A single train/test split invites you to tune until that one
period looks good, which is just overfitting in slow motion.

**3. Costs, charged on every position change.** Entering costs one side.
Flipping from long to short costs two. Shorts pay financing every bar. The
report shows the total cost drag in percent of capital, which is often larger
than the entire gross return.

### Deciding whether a result is real

The report ends in six checks:

```
[PASS] net Sharpe is positive
[PASS] beats buy & hold on Sharpe
[PASS] clears break-even accuracy
[FAIL] permutation p < 0.05
[FAIL] P(true Sharpe > 0) > 95%
[FAIL] bootstrap CI excludes zero
```

- **Permutation test** shuffles *when* the positions were held, keeping the
  same mix. If randomly reordering your trades does just as well, your timing
  carries no information no matter how the equity curve looks.
- **Block bootstrap** resamples returns in blocks to build a Sharpe confidence
  interval that respects autocorrelation. On a few hundred bars this interval
  is usually humblingly wide.
- **Probabilistic Sharpe ratio** adjusts for sample length, skew and fat
  tails, all of which flatter a naive Sharpe on crypto returns.

Pass `--trials-run N` with the number of configurations you have tried in
total. The report then prints the Sharpe ratio that a search over `N`
worthless strategies would be expected to produce by luck alone. Search 50
variants and that number is around 1.8: any "winner" below it is noise you
selected for. This is the most commonly ignored statistic in retail
backtesting and the reason most published strategies do not survive contact
with a live account.

## Useful invocations

```bash
# Offline, fully reproducible, no network needed
python3 -m crypto --source synthetic --bars 1500 --seed 42

# Cache real data once, then iterate against the local file
python3 -m crypto --source coinbase --symbol ETH-USD --bars 1200 --save-csv eth.csv
python3 -m crypto --source csv --csv eth.csv --folds

# Spot account: long or flat only, no shorting, no leverage
python3 -m crypto --source csv --csv eth.csv --max-position 1.0

# Size by conviction and target 40% annualized volatility
python3 -m crypto --source csv --csv eth.csv --mode proportional --target-vol 0.40

# Zero-fee fantasy, to see how much of the result is just the cost model
python3 -m crypto --source csv --csv eth.csv --fee-bps 0 --slippage-bps 0

# Today's paper position, plus the features driving it
python3 -m crypto --source csv --csv eth.csv --signal
```

Every knob that changes the answer is a flag. Change `--fee-bps` and watch a
"profitable" strategy die; that fragility is information about the strategy,
not a flaw in the tool.

## Extending it

The pieces are deliberately small and independent:

- **New features** go in `features.py`. Add the name to `FEATURE_NAMES` and
  the value to `feature_row`, in the same position. Only use `bars[k]` for
  `k <= i`. The causality tests will catch you if you slip.
- **A different model** needs `fit(X, y)` and `predict_proba(X)`. Swap it into
  `Pipeline` and nothing else changes. Note that a bigger model usually buys a
  better in-sample fit and a worse out-of-sample one.
- **Different sizing** goes in `size_positions`.
- **Other assets** work as-is; try several, because a strategy that only works
  on one symbol is usually fitted to that symbol's history.

## Honest ways people do make money in crypto

None of them are a prediction model, and all of them are work:

- **Holding**, sized so a 70% drawdown does not force you to sell. That has
  happened more than once and will happen again.
- **Market making and arbitrage**, which are real businesses competing on
  latency and fee tiers against firms with colocated servers.
- **Building things** for the industry, where the pay does not depend on
  guessing the price.
- **Staking and lending yields**, where the yield is compensation for real
  risks (protocol failure, counterparty default, token depreciation) that
  periodically materialize.

If you want to trade anyway: paper trade this for several months first, then
risk only what you can lose entirely, and keep position sizes small enough
that being wrong repeatedly is survivable. Being wrong repeatedly is the
normal case.

## Disclaimer

Research and educational software. Not financial, investment, or tax advice.
Backtested results are hypothetical and carry no implication of future
returns. Crypto assets are volatile and you can lose everything. You are
responsible for your own decisions and for complying with the rules in your
jurisdiction.

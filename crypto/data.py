"""OHLCV data loading: local CSV, public exchange APIs, or synthetic series.

No API keys are used or accepted anywhere in this module. Only public,
unauthenticated market-data endpoints are called.
"""

from __future__ import annotations

import csv
import json
import math
import os
import random
import ssl
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Sequence

SECONDS_PER_YEAR = 365.25 * 24 * 3600
USER_AGENT = "crypto-research-harness/1.0 (+stdlib urllib)"


@dataclass(frozen=True)
class Bar:
    """One OHLCV candle. `ts` is the bar's open time as a UNIX timestamp."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class DataError(RuntimeError):
    """Raised when market data cannot be loaded or fails validation."""


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------


def validate(bars: Sequence[Bar]) -> List[Bar]:
    """Sort by time, drop duplicate timestamps and obviously broken candles."""
    clean: List[Bar] = []
    seen = set()
    for bar in sorted(bars, key=lambda b: b.ts):
        if bar.ts in seen:
            continue
        if bar.close <= 0 or bar.open <= 0 or bar.high <= 0 or bar.low <= 0:
            continue
        if not all(math.isfinite(v) for v in (bar.open, bar.high, bar.low, bar.close)):
            continue
        if bar.high < bar.low:
            continue
        seen.add(bar.ts)
        clean.append(bar)
    return clean


def median_interval_seconds(bars: Sequence[Bar]) -> float:
    """Median spacing between consecutive bars, in seconds."""
    if len(bars) < 2:
        raise DataError("need at least two bars to infer the sampling interval")
    gaps = [bars[i + 1].ts - bars[i].ts for i in range(len(bars) - 1)]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        raise DataError("bar timestamps are not strictly increasing")
    return float(statistics.median(gaps))


def periods_per_year(bars: Sequence[Bar]) -> float:
    """How many bars of this size fit in a year (crypto trades 24/7)."""
    return SECONDS_PER_YEAR / median_interval_seconds(bars)


def closes(bars: Sequence[Bar]) -> List[float]:
    return [b.close for b in bars]


def simple_returns(bars: Sequence[Bar]) -> List[float]:
    """Close-to-close simple returns; element i is the return over bar i."""
    out = [0.0]
    for i in range(1, len(bars)):
        out.append(bars[i].close / bars[i - 1].close - 1.0)
    return out


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

_COLUMN_ALIASES = {
    "ts": {"ts", "time", "timestamp", "date", "datetime", "open_time"},
    "open": {"open", "o"},
    "high": {"high", "h"},
    "low": {"low", "l"},
    "close": {"close", "c", "price"},
    "volume": {"volume", "v", "vol", "base_volume"},
}


def _parse_timestamp(raw: str) -> int:
    raw = raw.strip()
    try:
        value = float(raw)
        # Heuristic: treat very large numbers as milliseconds.
        return int(value / 1000) if value > 1e11 else int(value)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(time.mktime(time.strptime(raw[: len(fmt) + 4], fmt)))
        except ValueError:
            continue
    raise DataError(f"unrecognized timestamp format: {raw!r}")


def load_csv(path: str) -> List[Bar]:
    """Load candles from a CSV with a header row.

    Column names are matched case-insensitively against common aliases, so
    exports from most data vendors load without editing.
    """
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise DataError(f"{path} has no header row")
        lookup = {}
        for field in reader.fieldnames:
            key = field.strip().lower()
            for canonical, aliases in _COLUMN_ALIASES.items():
                if key in aliases:
                    lookup[canonical] = field
        missing = [c for c in ("ts", "close") if c not in lookup]
        if missing:
            raise DataError(f"{path} is missing required column(s): {missing}")

        bars: List[Bar] = []
        for row in reader:
            close = float(row[lookup["close"]])
            bars.append(
                Bar(
                    ts=_parse_timestamp(row[lookup["ts"]]),
                    open=float(row[lookup["open"]]) if "open" in lookup else close,
                    high=float(row[lookup["high"]]) if "high" in lookup else close,
                    low=float(row[lookup["low"]]) if "low" in lookup else close,
                    close=close,
                    volume=float(row[lookup["volume"]]) if "volume" in lookup else 0.0,
                )
            )
    return validate(bars)


def save_csv(bars: Sequence[Bar], path: str) -> None:
    """Cache candles locally so research runs are reproducible offline."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "open", "high", "low", "close", "volume"])
        for b in bars:
            writer.writerow([b.ts, b.open, b.high, b.low, b.close, b.volume])


# --------------------------------------------------------------------------
# Public HTTP endpoints (no authentication)
# --------------------------------------------------------------------------


def _ssl_context() -> ssl.SSLContext:
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca and os.path.exists(ca):
        return ssl.create_default_context(cafile=ca)
    return ssl.create_default_context()


def _get_json(url: str, timeout: float = 30.0):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DataError(f"HTTP {exc.code} from {url}") from exc
    except Exception as exc:  # network, TLS, DNS, proxy
        raise DataError(f"request to {url} failed: {exc}") from exc


def fetch_coinbase(product: str = "BTC-USD", granularity: int = 86400, bars: int = 1000) -> List[Bar]:
    """Public Coinbase Exchange candles. Paginates 300 bars at a time."""
    out: List[Bar] = []
    end = int(time.time())
    while len(out) < bars:
        chunk = min(300, bars - len(out))
        start = end - chunk * granularity
        url = (
            f"https://api.exchange.coinbase.com/products/{product}/candles"
            f"?granularity={granularity}"
            f"&start={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(start))}"
            f"&end={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(end))}"
        )
        rows = _get_json(url)
        if not rows:
            break
        for row in rows:  # [time, low, high, open, close, volume]
            out.append(
                Bar(
                    ts=int(row[0]),
                    low=float(row[1]),
                    high=float(row[2]),
                    open=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        end = start
        time.sleep(0.35)  # stay well inside the public rate limit
    return validate(out)


def fetch_binance(symbol: str = "BTCUSDT", interval: str = "1d", bars: int = 1000) -> List[Bar]:
    """Public Binance klines. One request returns up to 1000 bars."""
    out: List[Bar] = []
    end_ms = int(time.time() * 1000)
    while len(out) < bars:
        limit = min(1000, bars - len(out))
        url = (
            f"https://api.binance.com/api/v3/klines?symbol={symbol}"
            f"&interval={interval}&limit={limit}&endTime={end_ms}"
        )
        rows = _get_json(url)
        if not rows:
            break
        for row in rows:
            out.append(
                Bar(
                    ts=int(row[0]) // 1000,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        end_ms = int(rows[0][0]) - 1
        time.sleep(0.35)
    return validate(out)


# --------------------------------------------------------------------------
# Synthetic data (offline development and tests)
# --------------------------------------------------------------------------


def synthetic_bars(
    n: int = 1500,
    seed: int = 7,
    start_price: float = 20000.0,
    annual_drift: float = 0.25,
    annual_vol: float = 0.70,
    interval: int = 86400,
    signal_strength: float = 0.0,
) -> List[Bar]:
    """Generate a regime-switching random walk that behaves like crypto.

    `signal_strength` injects a small, genuinely predictable autocorrelation
    into returns. It defaults to 0.0 (an efficient, unpredictable market), so
    tests that assert "no edge is found" are meaningful. Raise it only to
    verify that the harness can detect an edge that really exists.
    """
    rng = random.Random(seed)
    dt = interval / SECONDS_PER_YEAR
    mu = annual_drift * dt
    bars: List[Bar] = []
    price = start_price
    vol_state = annual_vol
    prev_shock = 0.0
    ts = int(time.time()) - n * interval

    for _ in range(n):
        # Volatility clusters: mean-reverting log-volatility.
        vol_state = math.exp(
            0.97 * math.log(vol_state) + 0.03 * math.log(annual_vol) + rng.gauss(0, 0.06)
        )
        sigma = vol_state * math.sqrt(dt)
        shock = rng.gauss(0.0, 1.0)
        log_ret = mu - 0.5 * sigma**2 + sigma * (shock + signal_strength * prev_shock)
        prev_shock = shock

        open_price = price
        close_price = open_price * math.exp(log_ret)
        wick = abs(rng.gauss(0.0, sigma * 0.5))
        high = max(open_price, close_price) * (1 + wick)
        low = min(open_price, close_price) * (1 - wick)
        volume = max(1.0, rng.lognormvariate(math.log(1000.0), 0.5))

        bars.append(Bar(ts=ts, open=open_price, high=high, low=low, close=close_price, volume=volume))
        price = close_price
        ts += interval

    return bars


def load_bars(
    source: str,
    symbol: str = "BTC-USD",
    interval: str = "1d",
    bars: int = 1000,
    csv_path: Optional[str] = None,
    seed: int = 7,
) -> List[Bar]:
    """Single entry point used by the CLI."""
    source = source.lower()
    if source == "csv":
        if not csv_path:
            raise DataError("source=csv requires --csv PATH")
        return load_csv(csv_path)
    if source == "synthetic":
        return synthetic_bars(n=bars, seed=seed)
    if source == "coinbase":
        granularity = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
        if interval not in granularity:
            raise DataError(f"coinbase supports intervals {sorted(granularity)}")
        return fetch_coinbase(symbol, granularity[interval], bars)
    if source == "binance":
        return fetch_binance(symbol.replace("-", ""), interval, bars)
    raise DataError(f"unknown source {source!r}")

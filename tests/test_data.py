"""Data loading, validation and the CLI wired end to end."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from crypto.cli import main
from crypto.data import (
    Bar,
    DataError,
    load_bars,
    load_csv,
    median_interval_seconds,
    periods_per_year,
    save_csv,
    simple_returns,
    synthetic_bars,
    validate,
)


class TestValidation(unittest.TestCase):
    def test_bars_are_sorted_by_time(self):
        unsorted_bars = [
            Bar(300, 1, 1, 1, 1, 1),
            Bar(100, 1, 1, 1, 1, 1),
            Bar(200, 1, 1, 1, 1, 1),
        ]
        self.assertEqual([b.ts for b in validate(unsorted_bars)], [100, 200, 300])

    def test_duplicate_timestamps_are_dropped(self):
        duplicated = [Bar(100, 1, 1, 1, 1, 1), Bar(100, 2, 2, 2, 2, 2)]
        self.assertEqual(len(validate(duplicated)), 1)

    def test_nonpositive_prices_are_dropped(self):
        self.assertEqual(validate([Bar(100, 1, 1, 1, 0.0, 1)]), [])

    def test_inverted_high_low_is_dropped(self):
        self.assertEqual(validate([Bar(100, 5, 1, 9, 5, 1)]), [])

    def test_interval_inference(self):
        bars = synthetic_bars(n=50, interval=3600)
        self.assertEqual(median_interval_seconds(bars), 3600.0)
        self.assertAlmostEqual(periods_per_year(bars), 8766.0, places=0)

    def test_interval_needs_two_bars(self):
        with self.assertRaises(DataError):
            median_interval_seconds([Bar(1, 1, 1, 1, 1, 1)])

    def test_simple_returns_match_the_closes(self):
        bars = [Bar(i * 86400, 1, 1, 1, price, 1) for i, price in enumerate([100.0, 110.0, 99.0])]
        returns = simple_returns(bars)
        self.assertEqual(returns[0], 0.0)
        self.assertAlmostEqual(returns[1], 0.10)
        self.assertAlmostEqual(returns[2], -0.10)


class TestCsv(unittest.TestCase):
    def test_round_trip(self):
        original = synthetic_bars(n=40, seed=2)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "bars.csv")
            save_csv(original, path)
            reloaded = load_csv(path)
        self.assertEqual(len(reloaded), len(original))
        self.assertAlmostEqual(reloaded[10].close, original[10].close, places=8)

    def test_alternative_column_names_and_iso_dates(self):
        content = (
            "Date,Open,High,Low,Close,Volume\n"
            "2024-01-01,100,105,99,104,10\n"
            "2024-01-02,104,108,103,107,12\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "vendor.csv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            bars = load_csv(path)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[1].close, 107.0)

    def test_millisecond_timestamps_are_detected(self):
        content = "timestamp,close\n1704067200000,100\n1704153600000,101\n"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ms.csv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            bars = load_csv(path)
        self.assertEqual(bars[0].ts, 1704067200)

    def test_missing_close_column_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "bad.csv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("date,volume\n2024-01-01,5\n")
            with self.assertRaises(DataError):
                load_csv(path)

    def test_csv_source_requires_a_path(self):
        with self.assertRaises(DataError):
            load_bars("csv")

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(DataError):
            load_bars("my-secret-alpha-feed")


class TestSynthetic(unittest.TestCase):
    def test_reproducible_for_a_given_seed(self):
        a = synthetic_bars(n=30, seed=99)
        b = synthetic_bars(n=30, seed=99)
        self.assertEqual([x.close for x in a], [x.close for x in b])

    def test_different_seeds_differ(self):
        a = synthetic_bars(n=30, seed=1)
        b = synthetic_bars(n=30, seed=2)
        self.assertNotEqual([x.close for x in a], [x.close for x in b])

    def test_prices_stay_positive_and_ohlc_is_consistent(self):
        for bar in synthetic_bars(n=400, seed=8):
            self.assertGreater(bar.low, 0)
            self.assertGreaterEqual(bar.high, max(bar.open, bar.close))
            self.assertLessEqual(bar.low, min(bar.open, bar.close))


class TestCli(unittest.TestCase):
    def test_offline_run_produces_a_report_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            json_path = os.path.join(directory, "metrics.json")
            equity_path = os.path.join(directory, "equity.csv")
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(
                    [
                        "--source", "synthetic", "--bars", "600",
                        "--train", "250", "--test", "50",
                        "--permutations", "30", "--bootstrap", "30",
                        "--iterations", "120", "--signal", "--folds",
                        "--json", json_path, "--equity-csv", equity_path,
                    ]
                )
            output = buffer.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("VERDICT", output)
            self.assertIn("BREAK-EVEN ACCURACY", output)
            self.assertIn("PAPER-TRADE SIGNAL", output)
            self.assertIn("Not financial advice", output)

            with open(json_path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertIn("strategy", payload)
            self.assertIn("permutation_p_value", payload)
            self.assertGreater(payload["out_of_sample_bars"], 0)

            with open(equity_path, encoding="utf-8") as handle:
                lines = handle.read().strip().split("\n")
            self.assertEqual(lines[0], "timestamp,position,forward_return,net_return,equity")
            self.assertEqual(len(lines) - 1, payload["out_of_sample_bars"])

    def test_insufficient_history_exits_with_an_error(self):
        buffer = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(errors):
            code = main(["--source", "synthetic", "--bars", "200", "--train", "500",
                         "--test", "50", "--quiet"])
        self.assertIn("only 200 bars available", errors.getvalue())
        self.assertEqual(code, 2)

    def test_banner_states_the_tool_cannot_trade(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main(["--source", "synthetic", "--bars", "400", "--train", "200", "--test", "50",
                  "--permutations", "0", "--bootstrap", "0", "--iterations", "80"])
        self.assertIn("never connects to a wallet", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()


class TestExchangeParsing(unittest.TestCase):
    """Parse recorded response shapes without touching the network.

    Coinbase returns [time, low, high, open, close, volume] while Binance
    returns [openTime, open, high, low, close, volume, ...]. Mixing the two
    orderings silently swaps highs and lows, so both are pinned here.
    """

    def test_coinbase_field_order(self):
        from unittest import mock

        from crypto import data as data_module

        rows = [
            [1704153600, 90.0, 110.0, 95.0, 105.0, 42.0],
            [1704067200, 80.0, 100.0, 85.0, 95.0, 40.0],
        ]
        with mock.patch.object(data_module, "_get_json", return_value=rows):
            bars = data_module.fetch_coinbase("BTC-USD", 86400, bars=2)

        self.assertEqual(len(bars), 2)
        first = bars[0]
        self.assertEqual(first.ts, 1704067200)  # sorted oldest first
        self.assertEqual(first.low, 80.0)
        self.assertEqual(first.high, 100.0)
        self.assertEqual(first.open, 85.0)
        self.assertEqual(first.close, 95.0)
        self.assertEqual(first.volume, 40.0)

    def test_binance_field_order_and_millisecond_time(self):
        from unittest import mock

        from crypto import data as data_module

        rows = [
            [1704067200000, "85.0", "100.0", "80.0", "95.0", "40.0", 1704153599999],
            [1704153600000, "95.0", "110.0", "90.0", "105.0", "42.0", 1704239999999],
        ]
        with mock.patch.object(data_module, "_get_json", return_value=rows):
            bars = data_module.fetch_binance("BTCUSDT", "1d", bars=2)

        self.assertEqual(len(bars), 2)
        first = bars[0]
        self.assertEqual(first.ts, 1704067200)  # milliseconds converted to seconds
        self.assertEqual(first.open, 85.0)
        self.assertEqual(first.high, 100.0)
        self.assertEqual(first.low, 80.0)
        self.assertEqual(first.close, 95.0)

    def test_an_empty_response_ends_pagination(self):
        from unittest import mock

        from crypto import data as data_module

        with mock.patch.object(data_module, "_get_json", return_value=[]):
            self.assertEqual(data_module.fetch_coinbase("BTC-USD", 86400, bars=100), [])

    def test_a_blocked_host_raises_a_clear_error(self):
        from unittest import mock

        from crypto import data as data_module

        with mock.patch.object(
            data_module, "_get_json", side_effect=DataError("HTTP 403 from example")
        ):
            with self.assertRaises(DataError):
                data_module.fetch_binance("BTCUSDT", "1d", bars=10)

    def test_coinbase_rejects_an_unsupported_interval(self):
        with self.assertRaises(DataError):
            load_bars("coinbase", interval="3d")

"""
Runs all four strategies (see backtest.py's STRATEGIES) across several tickers and prints
one aggregated table -- this is the actual point of having candidate strategies at all: a
strategy that only looks good on one ticker is very likely noise, not edge (see
momentum's Sharpe going from 1.845 on SPY to 0.745 on QQQ -- exactly the trap this script
exists to catch before it costs anything real).

Fetches whatever tickers aren't already cached in data/, then runs backtest.run() with each
strategy's own defaults (no per-ticker tuning -- that would just be overfitting one level up)
against each one, and prints both the per-ticker breakdown and an aggregate (mean Sharpe,
mean return, how many tickers each strategy actually beat buy-and-hold on).
"""

import argparse
import pathlib
import statistics

import pandas as pd

from backtest import STRATEGIES, run, DATA_DIR
from fetch_data import fetch


def buy_and_hold_return(csv_path: pathlib.Path) -> float:
    df = pd.read_csv(csv_path)
    return round(100 * (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1), 2)


def ensure_cached(ticker: str, period: str, start: str, end: str, interval: str) -> pathlib.Path:
    # Must match fetch_data.py's own naming exactly -- see that file's comment on why a bare
    # {ticker}.csv (no period/date-range encoded) caused a real stale-cache bug here.
    suffix = f"_{start}_{end}" if start else f"_{period}"
    path = DATA_DIR / f"{ticker.replace('=', '_')}{suffix}.csv"
    if not path.exists():
        fetch(ticker, period, interval, start, end)
    return path


def main(tickers: list[str], period: str, start: str, end: str, shares: int):
    results = {name: [] for name in STRATEGIES}
    bh_returns = {}

    for ticker in tickers:
        csv_path = ensure_cached(ticker, period, start, end, "1d")
        bh_returns[ticker] = buy_and_hold_return(csv_path)
        for name in STRATEGIES:
            _, summary = run(csv_path, name, shares, False, {}, quiet=True)
            summary["ticker"] = ticker
            summary["beat_buy_hold"] = summary["return_pct"] > bh_returns[ticker]
            results[name].append(summary)

    print(f"\n=== Per-ticker (buy-and-hold shown for reference) ===")
    header = f"{'Ticker':<7} {'B&H':>7}  " + "  ".join(f"{name:>11}" for name in STRATEGIES)
    print(header)
    for ticker in tickers:
        row = f"{ticker:<7} {bh_returns[ticker]:>6.1f}%  "
        for name in STRATEGIES:
            s = next(r for r in results[name] if r["ticker"] == ticker)
            mark = "*" if s["beat_buy_hold"] else " "
            row += f"{s['return_pct']:>9.1f}%{mark} "
        print(row)
    print("(* beat buy-and-hold on that ticker)")

    print(f"\n=== Aggregate across {len(tickers)} tickers ===")
    print(f"{'Strategy':<12} {'AvgReturn':>10} {'AvgSharpe':>10} {'AvgMaxDD':>9} {'BeatB&H':>8} {'TotalTrades':>12}")
    for name in STRATEGIES:
        rows = results[name]
        avg_return = statistics.mean(r["return_pct"] for r in rows)
        sharpes = [r["sharpe"] for r in rows if r["sharpe"] is not None]
        avg_sharpe = statistics.mean(sharpes) if sharpes else float("nan")
        avg_dd = statistics.mean(r["max_drawdown_pct"] for r in rows)
        beat_count = sum(1 for r in rows if r["beat_buy_hold"])
        total_trades = sum(r["total_trades"] for r in rows)
        print(f"{name:<12} {avg_return:>9.2f}% {avg_sharpe:>10.3f} {avg_dd:>8.2f}% "
              f"{beat_count:>5}/{len(tickers):<2} {total_trades:>12}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", nargs="+",
                         default=["SPY", "QQQ", "AAPL", "MSFT", "TSLA", "JPM", "XOM", "IWM"],
                         help="Space-separated tickers, e.g. --tickers AAPL MSFT GOOG")
    parser.add_argument("--period", default="2y", help="Relative lookback, e.g. 2y -- ignored if --start is given")
    parser.add_argument("--start", default=None, help="Explicit start date YYYY-MM-DD to isolate a specific historical window (e.g. a bear market)")
    parser.add_argument("--end", default=None, help="Explicit end date YYYY-MM-DD (used only with --start)")
    parser.add_argument("--shares", type=int, default=10)
    args = parser.parse_args()
    main(args.tickers, args.period, args.start, args.end, args.shares)

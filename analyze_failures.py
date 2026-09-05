"""
Runs the breakout strategy across tickers on a TRAIN window, pools every closed trade's
entry features (breakout margin %, volume ratio, trend strength %) against its outcome, and
compares winners vs losers -- "learn from failure" instead of just grid-searching exit
params. If losers cluster on some feature (e.g. consistently weaker volume), that's a real,
inspectable reason to add an entry filter, not a guess.

Only ever run this against a TRAIN window, then confirm whatever filter it suggests on a
separate TEST window (see the bottom of this file / tune_strategy.py's own pattern) -- a
threshold picked by looking at the SAME data used to judge it is exactly the overfitting
trap this whole project has been trying to avoid all night.
"""

import argparse
import statistics

from backtest import run
from compare_strategies import ensure_cached


def collect_closed_trades(tickers, start, end, shares, params):
    all_trades = []
    for ticker in tickers:
        csv_path = ensure_cached(ticker, None, start, end, "1d")
        result, _ = run(csv_path, "breakout", shares, False, params, quiet=True)
        for t in result.closed_trades:
            all_trades.append({**t, "ticker": ticker})
    return all_trades


def summarize(label, trades, feature):
    values = [t[feature] for t in trades]
    return f"{label:<8} n={len(trades):<4} avg={statistics.mean(values):>7.2f}  median={statistics.median(values):>7.2f}" if values else f"{label:<8} n=0"


def main(tickers, start, end, shares):
    trades = collect_closed_trades(tickers, start, end, shares, {})
    winners = [t for t in trades if t["won"]]
    losers = [t for t in trades if not t["won"]]

    print(f"\n{len(trades)} closed trades on {start} to {end} across {len(tickers)} tickers "
          f"({len(winners)} won, {len(losers)} lost, {100*len(winners)/len(trades):.1f}% win rate)\n")

    for feature in ("breakout_margin_pct", "volume_ratio", "trend_strength_pct"):
        print(f"-- {feature} --")
        print("  " + summarize("Winners", winners, feature))
        print("  " + summarize("Losers", losers, feature))
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", nargs="+", default=["SPY", "QQQ", "AAPL", "MSFT", "TSLA", "JPM", "XOM", "IWM"])
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2024-06-30")
    parser.add_argument("--shares", type=int, default=10)
    args = parser.parse_args()
    main(args.tickers, args.start, args.end, args.shares)

"""
"do the wider universe" (2026-09-05 evening) -- with shorts ruled out (three different
mechanisms all failed validation, see validate_short_strategies.py/strategies/breakdown.py),
the honest way left to get more trade opportunities without faking an edge is watching MORE
stocks with the SAME already-validated Breakout defaults (trend_period=50,
breakout_period=20, stop_pct=0.05, target_pct=0.08, max_hold_days=10) -- not re-tuning per
ticker (that would just be overfitting one ticker at a time), just checking whether the
existing rules generalize to a bigger, sector-diverse set.

Runs the fixed defaults on both the TRAIN and TEST windows for each CANDIDATE ticker (not
already in the original validated 8) and reports per-ticker + aggregate Sharpe/return/win
rate, so a decision about which ones to actually add to paper_trade_alpaca.py's watch list
is based on real numbers, not just "more tickers = more trades therefore good."
"""

import argparse
import statistics

from backtest import run
from compare_strategies import ensure_cached, buy_and_hold_return

# Sector-diverse, liquid large/mid-caps not already in the original validated 8
# (SPY/QQQ/AAPL/MSFT/TSLA/JPM/XOM/IWM) -- deliberately spread across tech, consumer,
# healthcare, industrials, and financials rather than piling onto one sector.
CANDIDATES = [
    "NVDA", "GOOGL", "AMZN", "META", "NFLX", "AMD",   # tech/growth
    "V", "MA", "BAC", "GS",                            # financials
    "HD", "WMT", "COST", "DIS", "PYPL",                # consumer
    "UNH", "JNJ", "PG", "KO",                          # healthcare/staples
    "BA", "CAT", "INTC", "F",                          # industrials/other
]


def evaluate(strategy, tickers, start, end, shares):
    rows = []
    for ticker in tickers:
        csv_path = ensure_cached(ticker, None, start, end, "1d")
        _, summary = run(csv_path, strategy, shares, False, {}, quiet=True)
        summary["beat_bh"] = summary["return_pct"] > buy_and_hold_return(csv_path)
        rows.append(summary)
    return rows


def aggregate(rows):
    sharpes = [r["sharpe"] if r["sharpe"] is not None else 0.0 for r in rows]
    return {
        "avg_return": statistics.mean(r["return_pct"] for r in rows),
        "avg_sharpe": statistics.mean(sharpes),
        "beat_bh": sum(1 for r in rows if r["beat_bh"]),
        "total_trades": sum(r["total_trades"] for r in rows),
    }


def main(tickers, train_start, train_end, test_start, test_end, shares):
    print(f"Testing Breakout's VALIDATED DEFAULTS (not re-tuned) on {len(tickers)} new tickers...")
    train_rows = evaluate("breakout", tickers, train_start, train_end, shares)
    test_rows = evaluate("breakout", tickers, test_start, test_end, shares)

    print(f"\n{'Ticker':<7} {'TrainSharpe':>11} {'TestSharpe':>11} {'TestReturn':>11} {'TestWinRate':>12} {'TestTrades':>11} {'BeatB&H':>8}")
    keep = []
    for tr, te in zip(train_rows, test_rows):
        ticker = te["ticker"].split("_")[0]
        wr = te["win_rate_pct"] if te["win_rate_pct"] is not None else float("nan")
        mark = "*" if te["beat_bh"] else " "
        good = (te["sharpe"] or 0) > 0 and (tr["sharpe"] or 0) > 0
        print(f"{ticker:<7} {tr['sharpe'] or 0:>11.3f} {te['sharpe'] or 0:>11.3f} {te['return_pct']:>10.2f}% "
              f"{wr:>11.1f}% {te['total_trades']:>11} {mark:>8}")
        if good:
            keep.append(ticker)

    train_agg, test_agg = aggregate(train_rows), aggregate(test_rows)
    print(f"\n=== Aggregate across all {len(tickers)} candidates ===")
    print(f"TRAIN avg_sharpe={train_agg['avg_sharpe']:.3f}  TEST avg_sharpe={test_agg['avg_sharpe']:.3f}  "
          f"TEST avg_return={test_agg['avg_return']:.2f}%  TEST beat_bh={test_agg['beat_bh']}/{len(tickers)}  "
          f"TEST total_trades={test_agg['total_trades']}")

    print(f"\nTickers with POSITIVE Sharpe on BOTH windows ({len(keep)}/{len(tickers)}): {', '.join(keep) if keep else 'none'}")
    print("(Positive-on-both is the bar for 'the existing rules hold up here' -- not the same as re-tuned/fully validated the way the original 8 were with a real grid search, just a sanity filter before adding a ticker to the live watch list.)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", nargs="+", default=CANDIDATES)
    parser.add_argument("--train-start", default="2021-01-01")
    parser.add_argument("--train-end", default="2024-06-30")
    parser.add_argument("--test-start", default="2024-07-01")
    parser.add_argument("--test-end", default="2026-09-01")
    parser.add_argument("--shares", type=int, default=10)
    args = parser.parse_args()
    main(args.tickers, args.train_start, args.train_end, args.test_start, args.test_end, args.shares)

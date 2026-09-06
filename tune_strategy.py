"""
Walk-forward parameter tuning: grid-search a strategy's params on a TRAIN window, pick
whichever combo had the best average Sharpe across tickers there, then evaluate that SAME
fixed combo on a separate, later TEST window it never saw during tuning -- also reporting
the strategy's plain defaults on that same test window for comparison.

This is the whole point of doing it this way rather than just grid-searching the full
history and picking whatever looks best: "the best rsi_period was 9" means nothing if it
was only ever checked against the same data it was chosen from. If the tuned combo's test-
window performance is close to (or worse than) the untuned defaults', that's the honest
answer -- the tuning found noise, not real edge, and the recommendation is to keep the
simpler defaults.
"""

import argparse
import statistics

from backtest import STRATEGIES, run
from compare_strategies import ensure_cached, buy_and_hold_return


def safe_run(csv_path, strategy, shares, params):
    """backtrader's built-in RSI indicator divides by zero on a perfectly monotonic
    up-only stretch within its lookback window (avg_loss == 0 exactly) -- rare with the
    default 14-day period, much more likely to actually get hit with a short one like 7,
    which is exactly why a grid search finds it and a single default-params run might not.
    Not something fixable in our own strategy code (it's inside backtrader's indicator
    engine), so this treats it as "this combo is numerically unstable on this ticker" --
    real information, not a bug to hide -- rather than crashing the whole sweep over it."""
    try:
        return run(csv_path, strategy, shares, False, params, quiet=True)
    except ZeroDivisionError:
        print(f"    [skipped: {csv_path.stem} params={params} -- RSI hit a divide-by-zero edge case]")
        return None, None

# Modest, hand-picked grids -- wide enough to matter, small enough to stay fast. Extend these
# directly if a strategy needs a wider search later.
PARAM_GRIDS = {
    "rsi": {
        "rsi_period": [7, 14, 21],
        "oversold": [20, 30],
        "exit_rsi": [50, 60],
    },
    "breakout": {
        "breakout_period": [10, 20, 30],
        "trend_period": [50, 100],
        "stop_pct": [0.03, 0.05, 0.08],
    },
    "breakdown": {
        "breakdown_period": [10, 20, 30],
        "trend_period": [50, 100],
        "stop_pct": [0.03, 0.05, 0.08],
    },
    "relief_short": {
        "overbought": [65, 70, 75],
        "exit_rsi": [35, 40, 45],
        "trend_period": [50, 100],
    },
    "momentum": {
        "lookback": [5, 10, 20],
        "entry_threshold": [0.02, 0.04, 0.06],
    },
    "macrossover": {
        "fast_period": [5, 10, 20],
        "slow_period": [30, 50, 100],
    },
}


def grid_combos(grid: dict) -> list[dict]:
    keys = list(grid)
    combos = [{}]
    for key in keys:
        combos = [dict(c, **{key: v}) for c in combos for v in grid[key]]
    return combos


def score_combo(strategy: str, params: dict, tickers: list[str], period: str, start: str, end: str, shares: int) -> float:
    """Average Sharpe across tickers -- None (no trades, or a numerically undefined ratio)
    counts as a hard 0, not "ignored", so a param combo that avoids trading altogether can't
    win by default just because it has no losing trades to count against it."""
    sharpes = []
    for ticker in tickers:
        csv_path = ensure_cached(ticker, period, start, end, "1d")
        _, summary = safe_run(csv_path, strategy, shares, params)
        if summary is None:
            continue
        sharpes.append(summary["sharpe"] if summary["sharpe"] is not None else 0.0)
    return statistics.mean(sharpes) if sharpes else float("-inf")


def evaluate(strategy: str, params: dict, tickers: list[str], period: str, start: str, end: str, shares: int) -> dict:
    returns, sharpes, drawdowns, beat_bh, counted = [], [], [], 0, 0
    for ticker in tickers:
        csv_path = ensure_cached(ticker, period, start, end, "1d")
        _, summary = safe_run(csv_path, strategy, shares, params)
        if summary is None:
            continue
        counted += 1
        returns.append(summary["return_pct"])
        sharpes.append(summary["sharpe"] if summary["sharpe"] is not None else 0.0)
        drawdowns.append(summary["max_drawdown_pct"])
        if summary["return_pct"] > buy_and_hold_return(csv_path):
            beat_bh += 1
    return {
        "avg_return": statistics.mean(returns) if returns else float("nan"),
        "avg_sharpe": statistics.mean(sharpes) if sharpes else float("nan"),
        "avg_drawdown": statistics.mean(drawdowns) if drawdowns else float("nan"),
        "beat_bh": beat_bh,
        "counted": counted,
    }


def main(strategy: str, tickers: list[str], train_start: str, train_end: str,
         test_start: str, test_end: str, shares: int):
    grid = PARAM_GRIDS[strategy]
    combos = grid_combos(grid)
    print(f"Grid-searching {strategy} -- {len(combos)} combos x {len(tickers)} tickers on "
          f"TRAIN window {train_start} to {train_end}...")

    best_combo, best_score = None, float("-inf")
    for combo in combos:
        score = score_combo(strategy, combo, tickers, None, train_start, train_end, shares)
        marker = ""
        if score > best_score:
            best_score, best_combo = score, combo
            marker = "  <- best so far"
        print(f"  {combo}  avg_sharpe={score:.3f}{marker}")

    print(f"\nBest on TRAIN: {best_combo} (avg Sharpe {best_score:.3f})")

    print(f"\n=== Evaluating on TEST window {test_start} to {test_end} (never seen during tuning) ===")
    tuned = evaluate(strategy, best_combo, tickers, None, test_start, test_end, shares)
    default = evaluate(strategy, {}, tickers, None, test_start, test_end, shares)

    print(f"{'':<10} {'AvgReturn':>10} {'AvgSharpe':>10} {'AvgDD':>8} {'BeatB&H':>8}")
    print(f"{'Tuned':<10} {tuned['avg_return']:>9.2f}% {tuned['avg_sharpe']:>10.3f} "
          f"{tuned['avg_drawdown']:>7.2f}% {tuned['beat_bh']:>5}/{tuned['counted']}")
    print(f"{'Default':<10} {default['avg_return']:>9.2f}% {default['avg_sharpe']:>10.3f} "
          f"{default['avg_drawdown']:>7.2f}% {default['beat_bh']:>5}/{default['counted']}")

    if tuned["avg_sharpe"] <= default["avg_sharpe"]:
        print("\nTuning did NOT beat the plain defaults on unseen data -- the grid search likely "
              "found noise in the training window, not real edge. Recommendation: keep the defaults.")
    else:
        print("\nTuned params beat the defaults on genuinely unseen data -- a real signal, "
              "though still just one test window's worth of evidence.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=list(PARAM_GRIDS), required=True)
    parser.add_argument("--tickers", nargs="+", default=["SPY", "QQQ", "AAPL", "MSFT", "TSLA", "JPM", "XOM", "IWM"])
    parser.add_argument("--train-start", default="2021-01-01")
    parser.add_argument("--train-end", default="2024-06-30")
    parser.add_argument("--test-start", default="2024-07-01")
    parser.add_argument("--test-end", default="2026-09-01")
    parser.add_argument("--shares", type=int, default=10)
    args = parser.parse_args()
    main(args.strategy, args.tickers, args.train_start, args.train_end,
         args.test_start, args.test_end, args.shares)

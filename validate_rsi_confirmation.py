"""
"Train him harder", round 2 (2026-09-05 evening): tests a genuinely different hypothesis
than the exit-shape experiments in validate_atr_exits.py -- does requiring RSI confirmation
at entry improve which breakouts get taken? Two failure modes this could catch that none of
the existing filters look at: a breakout with genuinely weak underlying momentum (low RSI
despite clearing the price level), or one that's already overbought/exhausted (very high
RSI, less room left before a pullback -- a different angle on the same "already run too far"
idea trend_strength_pct tested, but using momentum instead of distance-from-trend).

Same walk-forward discipline as every other validate_*.py here: score every candidate band
(including the current no-filter baseline) on the TRAIN window by average Sharpe, then
confirm the TRAIN-window winner -- not re-tuned -- against the SAME baseline on the held-out
TEST window it never saw.
"""

import argparse

from validate_entry_filters import evaluate

CANDIDATES = [
    ("baseline (no RSI filter)", {}),
    ("RSI >= 50 (require some momentum)", {"min_rsi": 50}),
    ("RSI <= 70 (avoid overbought)", {"max_rsi": 70}),
    ("RSI 50-70 (both)", {"min_rsi": 50, "max_rsi": 70}),
    ("RSI 40-80 (looser band)", {"min_rsi": 40, "max_rsi": 80}),
    ("RSI 55-65 (tight band)", {"min_rsi": 55, "max_rsi": 65}),
]


def main(tickers, train_start, train_end, test_start, test_end, shares):
    print(f"Scoring {len(CANDIDATES)} RSI configs on TRAIN window {train_start} to {train_end}...")

    best_label, best_params, best_score = None, None, float("-inf")
    for label, params in CANDIDATES:
        result = evaluate(params, tickers, train_start, train_end, shares)
        marker = ""
        if result["avg_sharpe"] > best_score:
            best_score, best_label, best_params = result["avg_sharpe"], label, params
            marker = "  <- best so far"
        print(f"  {label:<38} avg_sharpe={result['avg_sharpe']:.3f}  win_rate={result['win_rate']:.1f}%  "
              f"trades={result['total_trades']}{marker}")

    print(f"\nBest on TRAIN: {best_label} (avg Sharpe {best_score:.3f})")

    print(f"\n=== Evaluating on TEST window {test_start} to {test_end} (never seen during tuning) ===")
    winner = evaluate(best_params, tickers, test_start, test_end, shares)
    baseline = evaluate({}, tickers, test_start, test_end, shares)

    print(f"{'':<32} {'AvgReturn':>10} {'AvgSharpe':>10} {'WinRate':>8} {'Trades':>7}")
    print(f"{best_label[:32]:<32} {winner['avg_return']:>9.2f}% {winner['avg_sharpe']:>10.3f} "
          f"{winner['win_rate']:>7.1f}% {winner['total_trades']:>7}")
    print(f"{'Baseline (no filter)':<32} {baseline['avg_return']:>9.2f}% {baseline['avg_sharpe']:>10.3f} "
          f"{baseline['win_rate']:>7.1f}% {baseline['total_trades']:>7}")

    if best_label == "baseline (no RSI filter)" or winner["avg_sharpe"] <= baseline["avg_sharpe"]:
        print("\nNo RSI band beat the no-filter baseline on unseen data -- the TRAIN-window "
              "lean was noise. Recommendation: keep min_rsi=0/max_rsi=0 (no RSI filter).")
    else:
        print(f"\n{best_label} beat the baseline on genuinely unseen data -- worth switching to.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", nargs="+", default=["SPY", "QQQ", "AAPL", "MSFT", "TSLA", "JPM", "XOM", "IWM"])
    parser.add_argument("--train-start", default="2021-01-01")
    parser.add_argument("--train-end", default="2024-06-30")
    parser.add_argument("--test-start", default="2024-07-01")
    parser.add_argument("--test-end", default="2026-09-01")
    parser.add_argument("--shares", type=int, default=10)
    args = parser.parse_args()
    main(args.tickers, args.train_start, args.train_end, args.test_start, args.test_end, args.shares)

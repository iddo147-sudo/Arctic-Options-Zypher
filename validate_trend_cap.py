"""
Validates the "cap breakout entries near the trend line" filter analyze_failures.py's
2026-09-05 run suggested: winners averaged 6.15% above the trend SMA at entry, losers
7.42% -- a lean, not a cliff, so this checks it properly rather than trusting the number.

Same walk-forward discipline as tune_strategy.py: sweep candidate max_trend_strength_pct
caps (including 0 = uncapped, so "no cap wins" is a real possible outcome) on the TRAIN
window, pick whichever wins on average Sharpe, then confirm THAT cap -- not re-tuned --
against the plain uncapped baseline on the held-out TEST window it never saw.
"""

import argparse

from tune_strategy import evaluate

CANDIDATE_CAPS = [0, 6, 6.5, 7, 8, 10]


def main(tickers, train_start, train_end, test_start, test_end, shares):
    print(f"Sweeping max_trend_strength_pct caps {CANDIDATE_CAPS} on TRAIN window "
          f"{train_start} to {train_end}...")

    best_cap, best_score = None, float("-inf")
    for cap in CANDIDATE_CAPS:
        result = evaluate("breakout", {"max_trend_strength_pct": cap}, tickers, None, train_start, train_end, shares)
        marker = ""
        if result["avg_sharpe"] > best_score:
            best_score, best_cap = result["avg_sharpe"], cap
            marker = "  <- best so far"
        print(f"  cap={cap:<5} avg_sharpe={result['avg_sharpe']:.3f}  avg_return={result['avg_return']:.2f}%{marker}")

    print(f"\nBest cap on TRAIN: {best_cap} (avg Sharpe {best_score:.3f})")

    print(f"\n=== Evaluating on TEST window {test_start} to {test_end} (never seen during tuning) ===")
    capped = evaluate("breakout", {"max_trend_strength_pct": best_cap}, tickers, None, test_start, test_end, shares)
    baseline = evaluate("breakout", {"max_trend_strength_pct": 0}, tickers, None, test_start, test_end, shares)

    print(f"{'':<12} {'AvgReturn':>10} {'AvgSharpe':>10} {'AvgDD':>8} {'BeatB&H':>8}")
    print(f"{'Cap=' + str(best_cap):<12} {capped['avg_return']:>9.2f}% {capped['avg_sharpe']:>10.3f} "
          f"{capped['avg_drawdown']:>7.2f}% {capped['beat_bh']:>5}/{capped['counted']}")
    print(f"{'Uncapped':<12} {baseline['avg_return']:>9.2f}% {baseline['avg_sharpe']:>10.3f} "
          f"{baseline['avg_drawdown']:>7.2f}% {baseline['beat_bh']:>5}/{baseline['counted']}")

    if best_cap == 0 or capped["avg_sharpe"] <= baseline["avg_sharpe"]:
        print("\nThe cap did NOT beat the uncapped baseline on unseen data -- the TRAIN-window "
              "lean was noise, not real edge. Recommendation: leave max_trend_strength_pct at 0 (off).")
    else:
        print("\nThe cap beat the uncapped baseline on genuinely unseen data -- keep "
              f"max_trend_strength_pct={best_cap}.")


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

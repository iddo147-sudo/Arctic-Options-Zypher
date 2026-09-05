"""
"Train him harder" (2026-09-05 evening): tests whether volatility-scaled (ATR) stop/target
exits beat the current fixed-percentage ones (stop_pct=0.05/target_pct=0.08) that
tune_strategy.py already validated (Sharpe 3.188 on the TEST window). The fixed version
uses the SAME 5%/8% for every stock regardless of how volatile it actually trades -- tight
for something like TSLA, loose for a sleepy utility. ATR scales the exit to each stock's own
recent volatility instead. Also tests a pure trailing-stop variant (no fixed target at all,
let winners run) since that's a different shape of exit, not just a rescaled version of the
same one.

Same walk-forward discipline as validate_trend_cap.py/validate_entry_filters.py: score every
candidate (including the current fixed-% baseline) on the TRAIN window by average Sharpe,
then confirm the TRAIN-window winner -- not re-tuned -- against the SAME baseline on the
held-out TEST window it never saw.
"""

import argparse

from validate_entry_filters import evaluate  # same {avg_return, avg_sharpe, avg_drawdown, win_rate, total_trades, ...} helper

CANDIDATES = [
    ("baseline (fixed 5%/8%)", {}),
    ("ATR 1.5x/2.5x", {"atr_period": 14, "stop_atr_mult": 1.5, "target_atr_mult": 2.5}),
    ("ATR 2x/3x", {"atr_period": 14, "stop_atr_mult": 2.0, "target_atr_mult": 3.0}),
    ("ATR 2.5x/4x", {"atr_period": 14, "stop_atr_mult": 2.5, "target_atr_mult": 4.0}),
    ("ATR 2x + trailing 2x (no fixed target)", {"atr_period": 14, "stop_atr_mult": 2.0, "target_atr_mult": 0, "trailing_stop_atr_mult": 2.0}),
    ("ATR 2x + trailing 3x (no fixed target)", {"atr_period": 14, "stop_atr_mult": 2.0, "target_atr_mult": 0, "trailing_stop_atr_mult": 3.0}),
    ("ATR 1.5x + trailing 2.5x (no fixed target)", {"atr_period": 14, "stop_atr_mult": 1.5, "target_atr_mult": 0, "trailing_stop_atr_mult": 2.5}),
]


def main(tickers, train_start, train_end, test_start, test_end, shares):
    print(f"Scoring {len(CANDIDATES)} exit configs on TRAIN window {train_start} to {train_end}...")

    best_label, best_params, best_score = None, None, float("-inf")
    for label, params in CANDIDATES:
        result = evaluate(params, tickers, train_start, train_end, shares)
        marker = ""
        if result["avg_sharpe"] > best_score:
            best_score, best_label, best_params = result["avg_sharpe"], label, params
            marker = "  <- best so far"
        print(f"  {label:<45} avg_sharpe={result['avg_sharpe']:.3f}  win_rate={result['win_rate']:.1f}%  "
              f"trades={result['total_trades']}{marker}")

    print(f"\nBest on TRAIN: {best_label} (avg Sharpe {best_score:.3f})")

    print(f"\n=== Evaluating on TEST window {test_start} to {test_end} (never seen during tuning) ===")
    winner = evaluate(best_params, tickers, test_start, test_end, shares)
    baseline = evaluate({}, tickers, test_start, test_end, shares)

    print(f"{'':<32} {'AvgReturn':>10} {'AvgSharpe':>10} {'WinRate':>8} {'Trades':>7}")
    print(f"{best_label[:32]:<32} {winner['avg_return']:>9.2f}% {winner['avg_sharpe']:>10.3f} "
          f"{winner['win_rate']:>7.1f}% {winner['total_trades']:>7}")
    print(f"{'Baseline (fixed %)':<32} {baseline['avg_return']:>9.2f}% {baseline['avg_sharpe']:>10.3f} "
          f"{baseline['win_rate']:>7.1f}% {baseline['total_trades']:>7}")

    if best_label == "baseline (fixed 5%/8%)" or winner["avg_sharpe"] <= baseline["avg_sharpe"]:
        print("\nNo ATR-based exit beat the fixed-% baseline on unseen data -- the TRAIN-window "
              "lean was noise. Recommendation: keep atr_period=0 (fixed stop_pct/target_pct).")
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

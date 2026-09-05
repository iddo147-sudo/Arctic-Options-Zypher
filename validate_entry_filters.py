"""
Second "learn from failure" pass, after validate_trend_cap.py showed the trend-strength
cap was noise. This one tests the OTHER lead analyze_failures.py's 2026-09-05 run found:
breakout_margin_pct runs backwards from intuition -- losers cleared the breakout level by
MORE (avg 1.34%) than winners did (avg 1.05%) -- so this sweeps a CEILING on it
(max_breakout_margin_pct), not a floor.

Same walk-forward discipline as validate_trend_cap.py: sweep candidate caps (including 0 =
uncapped) on the TRAIN window by average Sharpe (not win rate -- that's the exact trap this
whole investigation started from), then confirm the TRAIN-window winner on the held-out TEST
window and report both Sharpe and win rate there.
"""

import argparse
import statistics

from backtest import run
from compare_strategies import ensure_cached, buy_and_hold_return

CANDIDATE_CAPS = [0, 0.8, 1.0, 1.2, 1.5, 2.0]


def evaluate(params, tickers, start, end, shares):
    returns, sharpes, drawdowns, beat_bh, counted = [], [], [], 0, 0
    total_won = total_trades = 0
    for ticker in tickers:
        csv_path = ensure_cached(ticker, None, start, end, "1d")
        _, summary = run(csv_path, "breakout", shares, False, params, quiet=True)
        counted += 1
        returns.append(summary["return_pct"])
        sharpes.append(summary["sharpe"] if summary["sharpe"] is not None else 0.0)
        drawdowns.append(summary["max_drawdown_pct"])
        total_won += summary["won"]
        total_trades += summary["total_trades"]
        if summary["return_pct"] > buy_and_hold_return(csv_path):
            beat_bh += 1
    return {
        "avg_return": statistics.mean(returns) if returns else float("nan"),
        "avg_sharpe": statistics.mean(sharpes) if sharpes else float("nan"),
        "avg_drawdown": statistics.mean(drawdowns) if drawdowns else float("nan"),
        "beat_bh": beat_bh,
        "counted": counted,
        "win_rate": 100 * total_won / total_trades if total_trades else float("nan"),
        "total_trades": total_trades,
    }


def main(tickers, train_start, train_end, test_start, test_end, shares):
    print(f"Sweeping max_breakout_margin_pct caps {CANDIDATE_CAPS} on TRAIN window "
          f"{train_start} to {train_end}...")

    best_cap, best_score = None, float("-inf")
    for cap in CANDIDATE_CAPS:
        result = evaluate({"max_breakout_margin_pct": cap}, tickers, train_start, train_end, shares)
        marker = ""
        if result["avg_sharpe"] > best_score:
            best_score, best_cap = result["avg_sharpe"], cap
            marker = "  <- best so far"
        print(f"  cap={cap:<5} avg_sharpe={result['avg_sharpe']:.3f}  win_rate={result['win_rate']:.1f}%  "
              f"trades={result['total_trades']}{marker}")

    print(f"\nBest cap on TRAIN: {best_cap} (avg Sharpe {best_score:.3f})")

    print(f"\n=== Evaluating on TEST window {test_start} to {test_end} (never seen during tuning) ===")
    capped = evaluate({"max_breakout_margin_pct": best_cap}, tickers, test_start, test_end, shares)
    baseline = evaluate({"max_breakout_margin_pct": 0}, tickers, test_start, test_end, shares)

    print(f"{'':<12} {'AvgReturn':>10} {'AvgSharpe':>10} {'WinRate':>8} {'Trades':>7} {'BeatB&H':>8}")
    print(f"{'Cap=' + str(best_cap):<12} {capped['avg_return']:>9.2f}% {capped['avg_sharpe']:>10.3f} "
          f"{capped['win_rate']:>7.1f}% {capped['total_trades']:>7} {capped['beat_bh']:>5}/{capped['counted']}")
    print(f"{'Uncapped':<12} {baseline['avg_return']:>9.2f}% {baseline['avg_sharpe']:>10.3f} "
          f"{baseline['win_rate']:>7.1f}% {baseline['total_trades']:>7} {baseline['beat_bh']:>5}/{baseline['counted']}")

    if best_cap == 0 or capped["avg_sharpe"] <= baseline["avg_sharpe"]:
        print("\nThe cap did NOT beat the uncapped baseline on unseen data -- the TRAIN-window "
              "lean was noise, not real edge. Recommendation: leave max_breakout_margin_pct at 0 (off).")
    else:
        print("\nThe cap beat the uncapped baseline on genuinely unseen data -- keep "
              f"max_breakout_margin_pct={best_cap}.")


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

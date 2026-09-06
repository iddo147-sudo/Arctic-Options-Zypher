"""
"should we add a stop-loss to RSI?" (2026-09-06). RSIReversion was validated with NO stop
at all -- pure RSI-recovery-or-max_hold_days exit. A stop is double-edged specifically on a
mean-reversion strategy: it protects against a stock that keeps falling past the oversold
entry, but can also cut a trade right before the bounce it was betting on.

Decides from the ORIGINAL 8-ticker TRAIN window (the same set RSI's own params -- rsi_period,
oversold, exit_rsi -- were actually tuned on in tune_strategy.py), confirms on that TEST
window, then separately reports how the TRAIN-chosen stop performs on RSI's actual LIVE
9-ticker universe (GOOGL/V/MA/GS/WMT/DIS/PG/BA/F, disjoint from Breakout's) for practical
relevance -- same walk-forward discipline as every other validate_*.py here.
"""

import argparse
import statistics

from backtest import run
from compare_strategies import ensure_cached, buy_and_hold_return

RSI_BASE_PARAMS = {"rsi_period": 14, "oversold": 30, "exit_rsi": 50}
CANDIDATE_STOPS = [0, 0.03, 0.05, 0.08, 0.10]
LIVE_TICKERS = ["GOOGL", "V", "MA", "GS", "WMT", "DIS", "PG", "BA", "F"]


def evaluate(tickers, stop_pct, start, end, shares=10):
    params = {**RSI_BASE_PARAMS, "stop_pct": stop_pct}
    returns, sharpes, total_won, total_trades, beat_bh = [], [], 0, 0, 0
    for ticker in tickers:
        csv_path = ensure_cached(ticker, None, start, end, "1d")
        _, summary = run(csv_path, "rsi", shares, False, params, quiet=True)
        returns.append(summary["return_pct"])
        sharpes.append(summary["sharpe"] if summary["sharpe"] is not None else 0.0)
        total_won += summary["won"]
        total_trades += summary["total_trades"]
        if summary["return_pct"] > buy_and_hold_return(csv_path):
            beat_bh += 1
    return {
        "avg_return": statistics.mean(returns),
        "avg_sharpe": statistics.mean(sharpes),
        "win_rate": 100 * total_won / total_trades if total_trades else float("nan"),
        "total_trades": total_trades,
        "beat_bh": beat_bh,
    }


def main(train_tickers, train_start, train_end, test_start, test_end):
    print(f"Scoring stop_pct candidates {CANDIDATE_STOPS} on the ORIGINAL 8-ticker TRAIN window "
          f"{train_start} to {train_end} (the set RSI's own params were tuned on)...")

    best_stop, best_score = None, float("-inf")
    for stop in CANDIDATE_STOPS:
        result = evaluate(train_tickers, stop, train_start, train_end)
        marker = ""
        if result["avg_sharpe"] > best_score:
            best_score, best_stop = result["avg_sharpe"], stop
            marker = "  <- best so far"
        print(f"  stop_pct={stop:<5} avg_sharpe={result['avg_sharpe']:.3f}  win_rate={result['win_rate']:.1f}%  "
              f"trades={result['total_trades']}{marker}")

    print(f"\nBest on TRAIN: stop_pct={best_stop} (avg Sharpe {best_score:.3f})")

    print(f"\n=== Confirming on the ORIGINAL 8's TEST window {test_start} to {test_end} ===")
    chosen = evaluate(train_tickers, best_stop, test_start, test_end)
    baseline = evaluate(train_tickers, 0, test_start, test_end)
    print(f"{'':<14} {'AvgReturn':>10} {'AvgSharpe':>10} {'WinRate':>8} {'Trades':>7}")
    print(f"{'stop=' + str(best_stop):<14} {chosen['avg_return']:>9.2f}% {chosen['avg_sharpe']:>10.3f} {chosen['win_rate']:>7.1f}% {chosen['total_trades']:>7}")
    print(f"{'No stop':<14} {baseline['avg_return']:>9.2f}% {baseline['avg_sharpe']:>10.3f} {baseline['win_rate']:>7.1f}% {baseline['total_trades']:>7}")

    verdict_holds = best_stop != 0 and chosen["avg_sharpe"] > baseline["avg_sharpe"]

    print(f"\n=== For reference: same stop_pct={best_stop} vs no-stop on the LIVE 9-ticker universe, TEST window ===")
    live_chosen = evaluate(LIVE_TICKERS, best_stop, test_start, test_end)
    live_baseline = evaluate(LIVE_TICKERS, 0, test_start, test_end)
    print(f"{'':<14} {'AvgReturn':>10} {'AvgSharpe':>10} {'WinRate':>8} {'Trades':>7}")
    print(f"{'stop=' + str(best_stop):<14} {live_chosen['avg_return']:>9.2f}% {live_chosen['avg_sharpe']:>10.3f} {live_chosen['win_rate']:>7.1f}% {live_chosen['total_trades']:>7}")
    print(f"{'No stop':<14} {live_baseline['avg_return']:>9.2f}% {live_baseline['avg_sharpe']:>10.3f} {live_baseline['win_rate']:>7.1f}% {live_baseline['total_trades']:>7}")

    if verdict_holds:
        print(f"\nstop_pct={best_stop} beat no-stop on the TRAIN-decided TEST window -- worth adopting.")
    else:
        print("\nNo stop beat the no-stop baseline on unseen data -- the TRAIN-window lean was noise. "
              "Recommendation: keep stop_pct=0 (no stop-loss), the already-validated version.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-tickers", nargs="+", default=["SPY", "QQQ", "AAPL", "MSFT", "TSLA", "JPM", "XOM", "IWM"])
    parser.add_argument("--train-start", default="2021-01-01")
    parser.add_argument("--train-end", default="2024-06-30")
    parser.add_argument("--test-start", default="2024-07-01")
    parser.add_argument("--test-end", default="2026-09-01")
    args = parser.parse_args()
    main(args.train_tickers, args.train_start, args.train_end, args.test_start, args.test_end)

"""
Re-test MACrossover with (a) a much wider ticker universe and (b) long/short as a grid
dimension, not a fixed assumption -- same TRAIN/TEST discipline as tune_strategy.py: the
TRAIN window decides params AND direction (long-only vs long+short), the TEST window
(never seen during the decision) confirms or rejects it.
"""

import statistics

from backtest import run
from compare_strategies import ensure_cached, buy_and_hold_return

TRAIN_START, TRAIN_END = "2021-01-01", "2024-06-30"
TEST_START, TEST_END = "2024-07-01", "2026-09-01"
SHARES = 10

TICKERS = [
    "SPY", "QQQ", "AAPL", "MSFT", "TSLA", "JPM", "XOM", "IWM", "AMZN", "META",
    "NFLX", "AMD", "BAC", "HD", "COST", "CAT", "GOOGL", "V", "MA", "GS", "WMT",
    "DIS", "PG", "BA", "F", "NVDA", "ORCL", "CRM", "ADBE", "INTC", "CSCO", "PEP",
    "KO", "MCD", "NKE", "UNH", "JNJ", "PFE", "LLY", "ABBV", "CVX", "WFC", "C",
    "MS", "T", "VZ", "TXN", "QCOM", "IBM", "GE", "HON", "LOW", "SBUX", "PYPL", "UPS",
]

GRID = {"fast_period": [5, 10, 20], "slow_period": [30, 50, 100]}


def combos():
    out = []
    for fast in GRID["fast_period"]:
        for slow in GRID["slow_period"]:
            for allow_short in (False, True):
                out.append({"fast_period": fast, "slow_period": slow, "allow_short": allow_short})
    return out


def safe_run(csv_path, params):
    allow_short = params["allow_short"]
    extra = {"fast_period": params["fast_period"], "slow_period": params["slow_period"]}
    try:
        return run(csv_path, "macrossover", SHARES, allow_short, extra, quiet=True)
    except ZeroDivisionError:
        return None, None


def score(params, tickers, start, end):
    sharpes = []
    for t in tickers:
        csv_path = ensure_cached(t, None, start, end, "1d")
        _, summary = safe_run(csv_path, params)
        if summary is None:
            continue
        sharpes.append(summary["sharpe"] if summary["sharpe"] is not None else 0.0)
    return statistics.mean(sharpes) if sharpes else float("-inf")


def evaluate(params, tickers, start, end):
    returns, sharpes, beat_bh, counted = [], [], 0, 0
    for t in tickers:
        csv_path = ensure_cached(t, None, start, end, "1d")
        _, summary = safe_run(csv_path, params)
        if summary is None:
            continue
        counted += 1
        returns.append(summary["return_pct"])
        sharpes.append(summary["sharpe"] if summary["sharpe"] is not None else 0.0)
        if summary["return_pct"] > buy_and_hold_return(csv_path):
            beat_bh += 1
    return {
        "avg_return": statistics.mean(returns) if returns else float("nan"),
        "avg_sharpe": statistics.mean(sharpes) if sharpes else float("nan"),
        "beat_bh": beat_bh,
        "counted": counted,
    }


def main():
    print(f"Grid-searching MACrossover -- {len(combos())} combos (long-only + long/short) "
          f"x {len(TICKERS)} tickers on TRAIN {TRAIN_START} to {TRAIN_END}...\n")

    best_combo, best_score = None, float("-inf")
    for c in combos():
        s = score(c, TICKERS, TRAIN_START, TRAIN_END)
        marker = ""
        if s > best_score:
            best_score, best_combo = s, c
            marker = "  <- best so far"
        direction = "long+short" if c["allow_short"] else "long-only"
        print(f"  fast={c['fast_period']:<3} slow={c['slow_period']:<3} {direction:<10} "
              f"avg_sharpe={s:.3f}{marker}")

    print(f"\nBest on TRAIN: {best_combo} (avg Sharpe {best_score:.3f})")

    print(f"\n=== Evaluating on TEST window {TEST_START} to {TEST_END} (never seen during search) ===")
    tuned = evaluate(best_combo, TICKERS, TEST_START, TEST_END)
    default_long = evaluate({"fast_period": 10, "slow_period": 30, "allow_short": False}, TICKERS, TEST_START, TEST_END)

    print(f"{'':<28} {'AvgReturn':>10} {'AvgSharpe':>10} {'BeatB&H':>10}")
    print(f"{'Tuned (wide+direction)':<28} {tuned['avg_return']:>9.2f}% {tuned['avg_sharpe']:>10.3f} "
          f"{tuned['beat_bh']:>7}/{tuned['counted']}")
    print(f"{'Default long-only (8-ticker)':<28} {default_long['avg_return']:>9.2f}% {default_long['avg_sharpe']:>10.3f} "
          f"{default_long['beat_bh']:>7}/{default_long['counted']}")

    if tuned["beat_bh"] == 0 or tuned["avg_sharpe"] <= default_long["avg_sharpe"]:
        print("\nStill doesn't clear the bar on unseen data -- wider universe and/or shorting "
              "did not fix the underlying problem. Recommendation: reject.")
    else:
        print("\nImprovement holds on genuinely unseen data -- worth a closer look.")


if __name__ == "__main__":
    main()

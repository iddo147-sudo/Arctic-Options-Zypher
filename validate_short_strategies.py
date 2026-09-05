"""
"we need it short expert too" (2026-09-05 evening) -- Breakdown's trend-following short
(strategies/breakdown.py) lost money outright in EVERY configuration tested
(validate result: negative Sharpe on every TRAIN combo, -1.047 on TEST). Before concluding
shorts just don't work here, this tests the two DIFFERENT short mechanisms already built
into this project via each strategy's own allow_short flag -- neither has actually been
walk-forward validated with shorting turned on before now (compare_strategies.py only ever
runs the allow_short=False defaults):

- RSIReversion with allow_short: fades OVERBOUGHT spikes (mean-reversion short) -- a
  structurally different bet than Breakdown's trend-following one.
- Momentum with allow_short: shorts DOWNSIDE momentum (trend-following, but on a much
  shorter lookback/threshold than Breakdown's 20-day breakout level).

Same TRAIN-then-TEST discipline as every other validate_*.py here, plus a same-window
long-only comparison for context (does adding shorting help or hurt each strategy overall).
"""

import argparse
import statistics

from backtest import run
from compare_strategies import ensure_cached, buy_and_hold_return


def evaluate(strategy, allow_short, tickers, start, end, shares):
    returns, sharpes, drawdowns, beat_bh, total_won, total_trades = [], [], [], 0, 0, 0
    for ticker in tickers:
        csv_path = ensure_cached(ticker, None, start, end, "1d")
        _, summary = run(csv_path, strategy, shares, allow_short, {}, quiet=True)
        returns.append(summary["return_pct"])
        sharpes.append(summary["sharpe"] if summary["sharpe"] is not None else 0.0)
        drawdowns.append(summary["max_drawdown_pct"])
        total_won += summary["won"]
        total_trades += summary["total_trades"]
        if summary["return_pct"] > buy_and_hold_return(csv_path):
            beat_bh += 1
    return {
        "avg_return": statistics.mean(returns),
        "avg_sharpe": statistics.mean(sharpes),
        "avg_drawdown": statistics.mean(drawdowns),
        "beat_bh": beat_bh,
        "win_rate": 100 * total_won / total_trades if total_trades else float("nan"),
        "total_trades": total_trades,
    }


def main(tickers, train_start, train_end, test_start, test_end, shares):
    for strategy in ("rsi", "momentum"):
        print(f"\n=== {strategy}, allow_short vs long-only (default params, not re-tuned) ===")

        # Decide from TRAIN only -- comparing on TEST directly would be exactly the
        # cherry-pick-from-the-holdout mistake this whole project has avoided all night.
        train_short = evaluate(strategy, True, tickers, train_start, train_end, shares)
        train_long = evaluate(strategy, False, tickers, train_start, train_end, shares)
        print(f"TRAIN long+short: avg_sharpe={train_short['avg_sharpe']:.3f}  avg_return={train_short['avg_return']:.2f}%")
        print(f"TRAIN long only:  avg_sharpe={train_long['avg_sharpe']:.3f}  avg_return={train_long['avg_return']:.2f}%")

        use_short = train_short["avg_sharpe"] > train_long["avg_sharpe"]
        decision = "long+short" if use_short else "long only"
        print(f"TRAIN says: {decision} wins -- confirming on TEST (never seen during that decision):")

        test_decided = evaluate(strategy, use_short, tickers, test_start, test_end, shares)
        test_other = evaluate(strategy, not use_short, tickers, test_start, test_end, shares)
        other_label = "long only" if use_short else "long+short"
        print(f"TEST {decision:<10}: avg_sharpe={test_decided['avg_sharpe']:.3f}  avg_return={test_decided['avg_return']:.2f}%  "
              f"win_rate={test_decided['win_rate']:.1f}%  trades={test_decided['total_trades']}  beat_bh={test_decided['beat_bh']}/{len(tickers)}")
        print(f"TEST {other_label:<10}: avg_sharpe={test_other['avg_sharpe']:.3f}  avg_return={test_other['avg_return']:.2f}%  "
              f"win_rate={test_other['win_rate']:.1f}%  trades={test_other['total_trades']}  beat_bh={test_other['beat_bh']}/{len(tickers)}")

        if use_short and test_decided["avg_sharpe"] > test_other["avg_sharpe"] and test_decided["avg_sharpe"] > 0:
            print(f"-> Shorting genuinely helped {strategy}: chosen by TRAIN, confirmed on TEST.")
        elif use_short:
            print(f"-> TRAIN preferred shorting for {strategy}, but that did NOT hold up on TEST -- noise, not edge.")
        else:
            print(f"-> TRAIN preferred long-only for {strategy} -- shorting never even got picked, no need to test it further.")


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

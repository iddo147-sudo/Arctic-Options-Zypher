"""
Universe expansion, same walk-forward discipline as tune_strategy.py -- but here the thing
being decided isn't a parameter, it's WHICH new tickers to add to an already-validated
strategy's live universe. Params are held fixed at whatever's actually live in
paper_trade_alpaca.py (Breakout's class defaults, RSI_PARAMS) -- we are not re-tuning them,
just asking "does this proven edge generalize to more symbols?"

A candidate ticker is added only if it clears the bar on BOTH windows:
  TRAIN (2021-01-01 to 2024-06-30): screens out tickers with no real edge there at all.
  TEST  (2024-07-01 to 2026-09-01): the held-out confirmation -- a candidate that only
  looked good on TRAIN and fails here is exactly the kind of noise this process exists
  to catch, same as it did for MACrossover's tuned params tonight.

Bar for "real edge" on a window: positive Sharpe AND beats buy-and-hold. Matches the
"is this actually good" standard used everywhere else this session.
"""

import argparse

from backtest import run
from compare_strategies import ensure_cached, buy_and_hold_return

BREAKOUT_PARAMS = {}  # Breakout.params class defaults -- nothing overridden live.
RSI_PARAMS = {"rsi_period": 14, "oversold": 30, "exit_rsi": 50, "max_hold_days": 10, "stop_pct": 0.05}

STRATEGY_PARAMS = {"breakout": BREAKOUT_PARAMS, "rsi": RSI_PARAMS}

# Already-live symbols, excluded as candidates -- widening means NEW tickers, not re-confirming these.
LIVE_SYMBOLS = {
    "SPY", "QQQ", "AAPL", "MSFT", "TSLA", "JPM", "XOM", "IWM", "AMZN", "META",
    "NFLX", "AMD", "BAC", "HD", "COST", "CAT", "GOOGL", "V", "MA", "GS", "WMT",
    "DIS", "PG", "BA", "F",
}

CANDIDATE_POOL = [
    "NVDA", "ORCL", "CRM", "ADBE", "INTC", "CSCO", "PEP", "KO", "MCD", "NKE",
    "UNH", "JNJ", "PFE", "LLY", "ABBV", "CVX", "WFC", "C", "MS", "T", "VZ",
    "TXN", "QCOM", "IBM", "GE", "HON", "LOW", "SBUX", "PYPL", "UPS",
]


def passes(strategy: str, shares: int, ticker: str, start: str, end: str) -> tuple[bool, dict]:
    csv_path = ensure_cached(ticker, None, start, end, "1d")
    try:
        _, summary = run(csv_path, strategy, shares, False, STRATEGY_PARAMS[strategy], quiet=True)
    except ZeroDivisionError:
        return False, {"skipped": "RSI divide-by-zero edge case"}
    if summary["total_trades"] == 0:
        return False, summary
    sharpe = summary["sharpe"] if summary["sharpe"] is not None else 0.0
    bh = buy_and_hold_return(csv_path)
    ok = sharpe > 0 and summary["return_pct"] > bh
    summary["buy_and_hold_pct"] = round(bh, 2)
    return ok, summary


def main(strategy: str, shares: int, train_start, train_end, test_start, test_end):
    candidates = [t for t in CANDIDATE_POOL if t not in LIVE_SYMBOLS]
    print(f"Screening {len(candidates)} candidate tickers for '{strategy}' "
          f"(fixed live params, not re-tuned)\n")

    train_pass = []
    print(f"=== TRAIN window {train_start} to {train_end} ===")
    for ticker in candidates:
        ok, summary = passes(strategy, shares, ticker, train_start, train_end)
        mark = "PASS" if ok else "fail"
        print(f"  {ticker:6s} {mark}  return={summary.get('return_pct')}%  "
              f"sharpe={summary.get('sharpe')}  b&h={summary.get('buy_and_hold_pct')}%")
        if ok:
            train_pass.append(ticker)

    print(f"\n{len(train_pass)}/{len(candidates)} passed TRAIN: {train_pass}")

    print(f"\n=== TEST window {test_start} to {test_end} (confirming TRAIN survivors only) ===")
    confirmed = []
    for ticker in train_pass:
        ok, summary = passes(strategy, shares, ticker, test_start, test_end)
        mark = "CONFIRMED" if ok else "rejected"
        print(f"  {ticker:6s} {mark}  return={summary.get('return_pct')}%  "
              f"sharpe={summary.get('sharpe')}  b&h={summary.get('buy_and_hold_pct')}%")
        if ok:
            confirmed.append(ticker)

    print(f"\n=== RESULT: {len(confirmed)}/{len(train_pass)} TRAIN-survivors confirmed on unseen TEST data ===")
    print(f"Add to {strategy} live universe: {confirmed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=list(STRATEGY_PARAMS), required=True)
    parser.add_argument("--shares", type=int, default=10)
    parser.add_argument("--train-start", default="2021-01-01")
    parser.add_argument("--train-end", default="2024-06-30")
    parser.add_argument("--test-start", default="2024-07-01")
    parser.add_argument("--test-end", default="2026-09-01")
    args = parser.parse_args()
    main(args.strategy, args.shares, args.train_start, args.train_end, args.test_start, args.test_end)

"""
Scheduled re-validation for the LIVE strategies -- the safe version of "make them learn and
improve" (see chat, 2026-09-10/11): this does NOT let an agent adjust itself based on recent
results, which is how ORB's TRAIN-decided "long+short" turned into a bad bet the moment the
market regime changed. Instead, on a schedule (meant to run quarterly via a Railway Cron
Schedule service, same pattern as the trading agents themselves), it re-checks the ALREADY-
VALIDATED strategies -- fixed params, fixed ticker universe, nothing re-tuned -- against the
most recent ~90 days of real data, and reports whether the edge that got them live is still
showing up. A human decides what to do with a decay flag; nothing here auto-adjusts anything.

Same universe/params as what's actually live -- imported straight from paper_trade_alpaca.py
rather than redeclared, so this can never silently drift from what's really trading.
"""

import datetime
import json
import os
import urllib.error
import urllib.request

import pandas as pd
from dotenv import load_dotenv

from backtest import run
from compare_strategies import ensure_cached
from paper_trade_alpaca import BREAKOUT_SYMBOLS, RSI_SYMBOLS, RSI_PARAMS, notify_phone

load_dotenv()

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "").rstrip("/")
AGENT_REPORT_TOKEN = os.environ.get("AGENT_REPORT_TOKEN", "")

AUDIT_WINDOW_DAYS = 90
# Fetched data has to start well before the audit window so each strategy's own lookback
# indicator (Breakout's 50-day trend SMA, RSI's 14-period average) is already warmed up BY
# the time the audit window begins -- otherwise most of the window gets spent computing the
# indicator instead of actually trading, and a strategy that never got a fair chance to
# trade looks identical to one that correctly chose not to. Bug caught during this file's
# own first test run (2026-09-11): a 90-day fetch with no buffer left Breakout's 50-day SMA
# barely computable at all, and most tickers showed a meaningless 0.0% "return".
WARMUP_BUFFER_DAYS = 200

STRATEGY_UNIVERSES = {
    "breakout": {"symbols": BREAKOUT_SYMBOLS, "params": {}, "shares": 10},
    "rsi": {"symbols": RSI_SYMBOLS, "params": RSI_PARAMS, "shares": 10},
}


def audit_window() -> tuple[str, str]:
    end = datetime.date.today() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=AUDIT_WINDOW_DAYS)
    return start.isoformat(), end.isoformat()


def audit_strategy(strategy: str, symbols: list[str], params: dict, shares: int, start: str, end: str) -> dict:
    fetch_start = (datetime.date.fromisoformat(start) - datetime.timedelta(days=WARMUP_BUFFER_DAYS)).isoformat()
    results = []
    for ticker in symbols:
        try:
            csv_path = ensure_cached(ticker, None, fetch_start, end, "1d")
            result, _ = run(csv_path, strategy, shares, False, params, quiet=True)
        except (ZeroDivisionError, SystemExit) as e:
            print(f"  [skipped] {ticker}: {e}")
            continue

        # Slice down to just the actual audit window -- both the strategy's own equity curve
        # (warmed up by the buffer, but only the tail matters) and buy&hold, computed fresh
        # from the raw prices so it's judged against the exact same window.
        equity = pd.DataFrame(result.equity_curve)
        equity["date"] = pd.to_datetime(equity["date"])
        window_equity = equity[equity["date"] >= start]
        if len(window_equity) < 2:
            print(f"  [skipped] {ticker}: not enough bars inside the audit window itself")
            continue

        base_value = window_equity["value"].iloc[0]
        end_value = window_equity["value"].iloc[-1]
        return_pct = round(100 * (end_value / base_value - 1), 2)

        daily_returns = window_equity["value"].pct_change().dropna()
        sharpe = (round(daily_returns.mean() / daily_returns.std() * (252 ** 0.5), 3)
                  if len(daily_returns) > 1 and daily_returns.std() > 0 else None)

        prices = pd.DataFrame(result.price_series)
        prices["date"] = pd.to_datetime(prices["date"])
        window_prices = prices[prices["date"] >= start]
        bh = round(100 * (window_prices["close"].iloc[-1] / window_prices["close"].iloc[0] - 1), 2)

        results.append({"ticker": ticker, "return_pct": return_pct, "sharpe": sharpe, "beat_bh": return_pct > bh})
        print(f"  {ticker:6s} return={return_pct}%  sharpe={sharpe}  "
              f"b&h={bh}%  {'BEAT' if return_pct > bh else 'lost to'} b&h")

    if not results:
        return {"strategy": strategy, "window": [start, end], "counted": 0, "decay_flag": True,
                "reason": "no tickers produced a usable backtest -- treat as a data problem, not a clean pass"}

    beat_count = sum(1 for r in results if r["beat_bh"])
    sharpes = [r["sharpe"] for r in results if r["sharpe"] is not None]
    avg_sharpe = sum(sharpes) / len(sharpes) if sharpes else None

    # Same reject bar used to screen every candidate strategy this project has ever tested --
    # applied here to an ALREADY-LIVE strategy instead of a new one, to catch decay early.
    decay_flag = beat_count == 0 or (avg_sharpe is not None and avg_sharpe <= 0)

    return {
        "strategy": strategy, "window": [start, end], "counted": len(results),
        "beat_bh_count": beat_count, "avg_sharpe": round(avg_sharpe, 3) if avg_sharpe is not None else None,
        "decay_flag": decay_flag, "results": results,
    }


def report(audit: dict):
    if not DASHBOARD_URL or not AGENT_REPORT_TOKEN:
        return
    req = urllib.request.Request(
        f"{DASHBOARD_URL}/api/report_revalidation",
        data=json.dumps(audit).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {AGENT_REPORT_TOKEN}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.URLError as e:
        print(f"[warn] failed to report revalidation to dashboard: {e}")


def main():
    start, end = audit_window()
    print(f"Quarterly re-validation -- auditing live strategies against {start} to {end} "
          f"(fixed params, no re-tuning)\n")

    any_decay = False
    for strategy, cfg in STRATEGY_UNIVERSES.items():
        print(f"=== {strategy} ({len(cfg['symbols'])} live tickers) ===")
        audit = audit_strategy(strategy, cfg["symbols"], cfg["params"], cfg["shares"], start, end)
        report(audit)

        if audit["decay_flag"]:
            any_decay = True
            msg = (f"{strategy}: beat buy&hold on {audit.get('beat_bh_count', 0)}/{audit['counted']} tickers, "
                   f"avg Sharpe {audit.get('avg_sharpe')} over the last {AUDIT_WINDOW_DAYS} days. "
                   f"Worth a look -- its edge may be fading.")
            print(f"\n[DECAY FLAG] {msg}\n")
            notify_phone(f"[{strategy}] possible strategy decay", msg, tags="warning")
        else:
            print(f"\n{strategy}: beat buy&hold on {audit['beat_bh_count']}/{audit['counted']} tickers, "
                  f"avg Sharpe {audit['avg_sharpe']} -- still holding up.\n")

    if not any_decay:
        notify_phone("Quarterly re-validation: all clear",
                      f"Breakout and RSI-Reversion both still clear the bar over the last "
                      f"{AUDIT_WINDOW_DAYS} days. No changes needed.", tags="white_check_mark")


if __name__ == "__main__":
    main()

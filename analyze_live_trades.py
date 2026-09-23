"""
2026-09-23, explicit user request -- "make the bots analyze the mistakes on the failed
trades": the REAL-trading counterpart to analyze_failures.py. That script compares winners vs
losers on a BACKTEST simulation; this one reads actual closed round-trips that
paper_trade_alpaca.py logged (via log_closed_trade()) and does the same winners-vs-losers
comparison on what really happened -- no simulation, no lookahead, just fewer trades (real
trading generates them one at a time, not thousands in a backtest window).

Reads from wherever the real data lives: locally against webapp/agent_closed_trades.json when
DASHBOARD_URL/AGENT_REPORT_TOKEN aren't set (plain local dev), or over HTTP against the live
Railway dashboard's /api/closed_trades when they are -- same "off/local unless configured"
pattern paper_trade_alpaca.py's own _get() helper already uses. Read-only either way: this
never trades and never writes.
"""
import argparse
import base64
import json
import os
import pathlib
import statistics
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

CLOSED_TRADES_PATH = pathlib.Path(__file__).parent / "webapp" / "agent_closed_trades.json"
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "").rstrip("/")
# DASHBOARD_PASSWORD, not AGENT_REPORT_TOKEN -- /api/closed_trades is require_reader-gated
# (same as /api/live_trades), since this is a READ. The agent token can also WRITE trades/kill
# -switch state, so it stays out of anything that isn't actually reporting live agent activity.
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

# Which entry features exist per strategy -- see paper_trade_alpaca.py's check_breakout_symbol
# (mirrors strategies/breakout.py's own three) and the rsi pending-entry loop.
FEATURES_BY_STRATEGY = {
    "breakout": ("breakout_margin_pct", "volume_ratio", "trend_strength_pct"),
    "rsi": ("rsi_at_entry",),
}


def load_trades() -> list[dict]:
    if DASHBOARD_URL and DASHBOARD_PASSWORD:
        basic = base64.b64encode(f":{DASHBOARD_PASSWORD}".encode()).decode()
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/closed_trades",
            headers={"Authorization": f"Basic {basic}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"[warn] could not reach dashboard at {DASHBOARD_URL}, falling back to local file: {e}")

    if not CLOSED_TRADES_PATH.exists():
        return []
    return json.loads(CLOSED_TRADES_PATH.read_text())


def summarize(label: str, trades: list[dict], feature: str) -> str:
    values = [t[feature] for t in trades if feature in t and t[feature] is not None]
    if not values:
        return f"  {label:<8} n=0"
    return (f"  {label:<8} n={len(values):<4} avg={statistics.mean(values):>7.2f}  "
            f"median={statistics.median(values):>7.2f}")


def main(strategy: str | None):
    trades = load_trades()
    if strategy:
        trades = [t for t in trades if t["strategy"] == strategy]

    if not trades:
        print("No real closed trades yet -- nothing to analyze. "
              "(Open positions that haven't exited yet don't count as failures or wins.)")
        return

    for strat, features in FEATURES_BY_STRATEGY.items():
        strat_trades = [t for t in trades if t["strategy"] == strat]
        if not strat_trades:
            continue
        winners = [t for t in strat_trades if t["won"]]
        losers = [t for t in strat_trades if not t["won"]]
        win_rate = 100 * len(winners) / len(strat_trades)

        print(f"\n=== {strat} ===")
        print(f"{len(strat_trades)} closed trades ({len(winners)} won, {len(losers)} lost, "
              f"{win_rate:.1f}% win rate)\n")

        if not losers:
            print("  No losers yet -- nothing to compare winners against.\n")
        else:
            for feature in features:
                print(f"-- {feature} --")
                print(summarize("Winners", winners, feature))
                print(summarize("Losers", losers, feature))
                print()

        print("Trades:")
        for t in sorted(strat_trades, key=lambda t: (t["exit_date"] or "")):
            mark = "WIN " if t["won"] else "LOSS"
            print(f"  [{mark}] {t['symbol']:<6} {t['entry_date']} -> {t['exit_date']}  "
                  f"{t['entry_price']:>9.2f} -> {t['exit_price']:>9.2f}  "
                  f"pnl={t['pnl_pct']:>+6.2f}%  reason={t['exit_reason']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=list(FEATURES_BY_STRATEGY), default=None)
    args = parser.parse_args()
    main(args.strategy)

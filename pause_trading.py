"""
2026-09-29, explicit user request -- "stop new positions for 5 days period to analyze markets
through news only": the human-facing side of the manual pause. paper_trade_alpaca.py's
check_manual_pause() only ever READS this state; this script is how a human actually sets or
lifts it. Kept ALONGSIDE the automatic monthly profit lock (see check_profit_lock()), not
instead of it -- explicit user choice, "both keep auto lock".

Talks to the live Railway dashboard over HTTP (DASHBOARD_URL + DASHBOARD_PASSWORD, same
require_reader HTTP Basic auth as analyze_live_trades.py's read of /api/closed_trades) when
those are configured, falling back to writing webapp/manual_pause_state.json directly for
plain local dev -- same "off/local unless configured" pattern used everywhere else in this
project.

Usage:
    python pause_trading.py start --days 5 --reason "news-only review"
    python pause_trading.py status
    python pause_trading.py cancel
"""
import argparse
import base64
import datetime
import json
import pathlib
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

MANUAL_PAUSE_PATH = pathlib.Path(__file__).parent / "webapp" / "manual_pause_state.json"
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "").rstrip("/")
# DASHBOARD_PASSWORD, not AGENT_REPORT_TOKEN -- this is a human triggering a pause, gated by
# require_reader same as analyze_live_trades.py's read, not require_agent like the bot's own
# reports. See webapp/main.py's manual-pause section for why start/cancel are require_reader.
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")


def _remote_call(path: str, method: str, body: dict | None = None) -> dict:
    basic = base64.b64encode(f":{DASHBOARD_PASSWORD}".encode()).decode()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{DASHBOARD_URL}{path}",
        data=data,
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def start(days: int, reason: str | None):
    if DASHBOARD_URL and DASHBOARD_PASSWORD:
        try:
            result = _remote_call("/api/manual_pause/start", "POST", {"days": days, "reason": reason})
            print(f"Manual pause started -- no new entries until {result['paused_until']} (dashboard).")
            return
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
            print(f"[warn] could not reach dashboard at {DASHBOARD_URL}, falling back to local file: {e}")

    started_at = datetime.datetime.now(datetime.timezone.utc)
    paused_until = started_at + datetime.timedelta(days=days)
    MANUAL_PAUSE_PATH.write_text(json.dumps({
        "paused_until": paused_until.isoformat(),
        "reason": reason or f"manual {days}-day pause",
        "started_at": started_at.isoformat(),
    }, indent=2))
    print(f"Manual pause started -- no new entries until {paused_until.isoformat()} (local file).")


def cancel():
    if DASHBOARD_URL and DASHBOARD_PASSWORD:
        try:
            _remote_call("/api/manual_pause/cancel", "POST")
            print("Manual pause cancelled (dashboard).")
            return
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"[warn] could not reach dashboard at {DASHBOARD_URL}, falling back to local file: {e}")

    if MANUAL_PAUSE_PATH.exists():
        MANUAL_PAUSE_PATH.unlink()
    print("Manual pause cancelled (local file).")


def status():
    if DASHBOARD_URL and DASHBOARD_PASSWORD:
        basic = base64.b64encode(f":{DASHBOARD_PASSWORD}".encode()).decode()
        req = urllib.request.Request(f"{DASHBOARD_URL}/api/manual_pause",
                                      headers={"Authorization": f"Basic {basic}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                state = json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"[warn] could not reach dashboard at {DASHBOARD_URL}, falling back to local file: {e}")
            state = json.loads(MANUAL_PAUSE_PATH.read_text()) if MANUAL_PAUSE_PATH.exists() else {}
    else:
        state = json.loads(MANUAL_PAUSE_PATH.read_text()) if MANUAL_PAUSE_PATH.exists() else {}

    paused_until = state.get("paused_until")
    if not paused_until:
        print("No manual pause active.")
        return
    active = datetime.datetime.now(datetime.timezone.utc) < datetime.datetime.fromisoformat(paused_until)
    print(f"{'ACTIVE' if active else 'expired'} -- reason: {state.get('reason')}, paused_until: {paused_until}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Pause new entries for N days (existing positions still monitored for exits).")
    p_start.add_argument("--days", type=int, default=5)
    p_start.add_argument("--reason", type=str, default=None)

    sub.add_parser("cancel", help="Lift an active manual pause early.")
    sub.add_parser("status", help="Show whether a manual pause is currently active.")

    args = parser.parse_args()
    if args.command == "start":
        start(args.days, args.reason)
    elif args.command == "cancel":
        cancel()
    elif args.command == "status":
        status()

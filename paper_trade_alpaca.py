"""
Runs the validated Breakout strategy live against Alpaca's PAPER trading account, across
the SAME 8-ticker set tune_strategy.py/compare_strategies.py validated it on (Sharpe 3.188,
61.6% win rate on unseen data) -- not the original single-symbol MACrossover version this
file used before 2026-09-05, and not just SPY alone (2026-09-05 later same day: watching one
ticker means one shot at a signal per day; the strategy was never validated as "trade SPY
specifically", it was validated across all 8).

Meant to be re-run on a schedule (cron/Task Scheduler), once per trading day after the
close -- NOT left running as a daemon at this stage, same design choice as the original
version. Each run is a fresh process, so exit logic (stop-loss/target/max_hold) needs
position state to survive between runs: Alpaca's own position record is the source of truth
for entry price (avg_entry_price) and quantity per symbol, but Alpaca doesn't track WHEN a
position was opened, so entry date per symbol is persisted locally in
agent_position_state.json (one small dict, keyed by symbol -- NOT one file per symbol, so a
single read/write covers the whole run).

SETUP: see README.md's Paper trading section for getting a free Alpaca paper key pair.
SAFETY: `paper=True` is hardcoded below, not a flag -- this script has no code path that can
submit a live order.
"""

import argparse
import datetime
import json
import pathlib
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from strategies.breakout import Breakout

load_dotenv()  # picks up .env for local runs -- setx env vars (if already set) still win, same as webapp/main.py's own load_dotenv() call

# The original 8 = tune_strategy.py/compare_strategies.py's default set, the one the
# strategy's PARAMS were actually grid-searched and validated against.
#
# The second 8 (2026-09-05 evening, "do the wider universe") = expand_universe.py's result:
# ran the SAME fixed, already-validated defaults (not re-tuned per ticker -- that would be
# overfitting one at a time) across 23 new sector-diverse candidates, kept only the ones
# with POSITIVE Sharpe on BOTH the TRAIN and TEST windows. 15 of 23 candidates failed that
# bar and are deliberately left out (e.g. NVDA, GOOGL, V, MA, JNJ, INTC, F, PYPL) -- this is
# not "watch everything for more trades", it's "watch more names the existing rules actually
# held up on." See expand_universe.py to re-run this check or test a different candidate list.
DEFAULT_SYMBOLS = [
    "SPY", "QQQ", "AAPL", "MSFT", "TSLA", "JPM", "XOM", "IWM",
    "AMZN", "META", "NFLX", "AMD", "BAC", "HD", "COST", "CAT",
]

WEBAPP_DIR = pathlib.Path(__file__).parent / "webapp"
STATUS_PATH = WEBAPP_DIR / "agent_status.json"
TRADES_PATH = WEBAPP_DIR / "agent_trades.json"
# {symbol: entry_date} -- everything else about an open position (qty, avg fill price) is
# read straight from Alpaca each run rather than duplicated here, so this file can never
# drift out of sync with what the broker actually thinks we hold.
POSITION_STATE_PATH = WEBAPP_DIR / "agent_position_state.json"

# Only set when this runs as a SEPARATE Railway service (a Cron Schedule) from the
# dashboard -- that's its own container with its own disk, so writing local files alone
# would never reach the dashboard's. When unset (the local-dev case), _report() is a no-op
# and the local file writes below are the only thing that happens, same as before this
# existed. See webapp/main.py's module docstring for the receiving end.
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "").rstrip("/")
AGENT_REPORT_TOKEN = os.environ.get("AGENT_REPORT_TOKEN", "")

# ntfy.sh: a free push-notification relay, no account/signup needed on either end -- POST to
# a topic URL, anyone subscribed to that exact topic (via the ntfy app or ntfy.sh/<topic> in
# a browser) gets a push. The topic name IS the access control (public service, no auth), so
# it needs to be an unguessable random string, not something like "my-trading-bot" -- unset
# means no notification is sent, same "off unless configured" pattern as DASHBOARD_URL above.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")


def notify_phone(title: str, message: str, tags: str = "moneybag"):
    if not NTFY_TOPIC:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Tags": tags},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.URLError as e:
        # Same "never let a notification hiccup fail the actual trading run" reasoning as
        # _report() below -- the order already went through (or didn't) either way.
        print(f"[warn] failed to send phone notification: {e}")


def _report(path: str, payload: dict):
    if not DASHBOARD_URL or not AGENT_REPORT_TOKEN:
        return
    req = urllib.request.Request(
        f"{DASHBOARD_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {AGENT_REPORT_TOKEN}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.URLError as e:
        # Never let a dashboard-reporting hiccup fail the actual trading run -- whatever
        # orders were going to fire already fired (or didn't) by the time this runs.
        print(f"[warn] failed to report to dashboard at {DASHBOARD_URL}: {e}")


def log_live_trade(symbol, side, price, size, reason):
    """Appends one real fill to agent_trades.json -- the dashboard's 'Live trades' panel,
    kept entirely separate from a backtest's trade_log so the two are never confused for
    each other. Includes symbol now that more than one ticker can trade in the same run."""
    trade = {
        "date": datetime.date.today().isoformat(),
        "symbol": symbol,
        "side": side,
        "price": round(price, 2),
        "size": size,
        "reason": reason,
    }
    trades = json.loads(TRADES_PATH.read_text()) if TRADES_PATH.exists() else []
    trades.append(trade)
    TRADES_PATH.write_text(json.dumps(trades, indent=2))
    _report("/api/report_trade", trade)

    # "submitted", not "bought"/"sold" -- 2026-09-05 real incident: a market order placed
    # while the market's closed sits ACCEPTED for hours before it actually fills, so this
    # notification fires on submission, same moment the order actually enters the market.
    verb = "Submitted BUY" if side == "BUY" else "Submitted SELL"
    notify_phone(
        title=f"{verb} {symbol}",
        message=f"{size} shares @ ${price:.2f} -- {reason}",
        tags="moneybag" if side == "BUY" else "chart_with_downwards_trend",
    )


def load_entry_dates() -> dict:
    if POSITION_STATE_PATH.exists():
        return json.loads(POSITION_STATE_PATH.read_text())
    return {}


def get_clients() -> tuple[TradingClient, StockHistoricalDataClient]:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit(
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables first -- "
            "see README.md's Paper trading section for where to get them."
        )
    trading = TradingClient(api_key, secret_key, paper=True)  # hardcoded -- see SAFETY note above
    data = StockHistoricalDataClient(api_key, secret_key)
    return trading, data


def check_symbol(trading, data_client, symbol: str, shares: int, entry_dates: dict) -> dict:
    """Runs the strategy's entry/exit check for ONE symbol and returns its status dict.
    Mutates entry_dates in place (add/remove this symbol's key) -- the caller persists the
    whole dict once after all symbols are checked, not per-symbol, so one run only ever
    does one disk write for this file regardless of how many tickers it watches."""
    p = Breakout.params
    lookback_days = max(p.trend_period, p.breakout_period) * 2  # generous buffer for weekends/holidays
    bars_request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.datetime.now() - datetime.timedelta(days=lookback_days),
    )
    bars = data_client.get_stock_bars(bars_request).df
    if bars is None or len(bars) < p.trend_period + 1:
        return {"error": f"not enough bars to compute the {p.trend_period}-day trend yet"}

    closes = bars["close"]
    today_close = float(closes.iloc[-1])
    trend_ma = float(closes.rolling(p.trend_period).mean().iloc[-1])
    # Highest close of the PRIOR breakout_period days, excluding today -- same convention as
    # strategies/breakout.py's own bt.indicators.Highest(self.data.close(-1), ...).
    highest = float(closes.iloc[-(p.breakout_period + 1):-1].max())

    try:
        position = trading.get_open_position(symbol)
        current_qty = int(float(position.qty))
        avg_entry_price = float(position.avg_entry_price)
    except Exception:
        current_qty = 0
        avg_entry_price = None

    print(f"{datetime.date.today().isoformat()} {symbol} close={today_close:.2f} "
          f"trend_ma={trend_ma:.2f} highest={highest:.2f} position={current_qty}")

    if current_qty == 0:
        entry_dates.pop(symbol, None)  # flat -- any stale entry_date no longer applies
        in_uptrend = today_close > trend_ma
        broke_out = today_close > highest
        if not (in_uptrend and broke_out):
            reason = "no breakout" if not broke_out else "below trend"
            print(f"{symbol}: no entry -- {reason}. Holding flat.")
            return {"position": 0, "close": round(today_close, 2), "trend_ma": round(trend_ma, 2),
                    "highest": round(highest, 2), "last_action": f"holding flat ({reason})"}

        # 2026-09-05 real incident: a BUY submitted while the market's closed sits as
        # ACCEPTED (not FILLED) for hours -- get_open_position() legitimately sees "no
        # position" the whole time, so re-running before it fills would submit a SECOND buy
        # for the same signal. Checking for an already-open order closes that gap.
        open_orders = trading.get_orders(GetOrdersRequest(
            symbols=[symbol], status=QueryOrderStatus.OPEN))
        if open_orders:
            print(f"{symbol}: breakout confirmed but an order is already pending (id {open_orders[0].id}) -- not submitting another.")
            return {"position": 0, "close": round(today_close, 2), "trend_ma": round(trend_ma, 2),
                    "highest": round(highest, 2), "last_action": "breakout confirmed, order already pending"}

        order = MarketOrderRequest(symbol=symbol, qty=shares, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        submitted = trading.submit_order(order)
        entry_dates[symbol] = datetime.date.today().isoformat()
        log_live_trade(symbol, "BUY", today_close, shares, "trend breakout")
        print(f"Submitted BUY {shares} {symbol} -- order id {submitted.id}")
        return {"position": shares, "close": round(today_close, 2), "trend_ma": round(trend_ma, 2),
                "highest": round(highest, 2), "last_action": f"BUY {shares} {symbol}",
                "last_order_id": str(submitted.id)}

    # In a position -- check exits. avg_entry_price comes straight from Alpaca (the real
    # fill price), not something this script tracked itself.
    entry_date_str = entry_dates.get(symbol)
    bars_held = (datetime.date.today() - datetime.date.fromisoformat(entry_date_str)).days if entry_date_str else None

    stopped_out = today_close <= avg_entry_price * (1 - p.stop_pct)
    hit_target = today_close >= avg_entry_price * (1 + p.target_pct)
    timed_out = bars_held is not None and bars_held >= p.max_hold_days

    if not (stopped_out or hit_target or timed_out):
        print(f"{symbol}: holding {current_qty} @ entry {avg_entry_price:.2f} -- no exit condition met.")
        return {"position": current_qty, "close": round(today_close, 2),
                "entry_price": round(avg_entry_price, 2), "last_action": "holding position"}

    reason = "stop-loss" if stopped_out else "profit target" if hit_target else "max hold days"

    # Same reasoning as the entry-side guard above -- a SELL submitted while the position
    # hasn't been reduced yet would otherwise stack a second closing order on top.
    open_orders = trading.get_orders(GetOrdersRequest(symbols=[symbol], status=QueryOrderStatus.OPEN))
    if open_orders:
        print(f"{symbol}: exit condition met but an order is already pending (id {open_orders[0].id}) -- not submitting another.")
        return {"position": current_qty, "close": round(today_close, 2), "entry_price": round(avg_entry_price, 2),
                "last_action": f"exit ({reason}) pending, order already in flight"}

    order = MarketOrderRequest(symbol=symbol, qty=abs(current_qty), side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
    submitted = trading.submit_order(order)
    entry_dates.pop(symbol, None)
    log_live_trade(symbol, "SELL", today_close, abs(current_qty), reason)
    print(f"Submitted SELL {abs(current_qty)} {symbol} -- order id {submitted.id} ({reason})")
    return {"position": 0, "close": round(today_close, 2),
            "last_action": f"SELL {abs(current_qty)} {symbol} ({reason})", "last_order_id": str(submitted.id)}


def run(symbols: list[str], shares: int):
    trading, data_client = get_clients()

    account = trading.get_account()
    buying_power = round(float(account.buying_power), 2)
    print(f"Connected to Alpaca paper account -- buying power ${buying_power:,.2f}")

    entry_dates = load_entry_dates()
    tickers = {}
    fired = []  # (symbol, action_text, order_id) for whichever symbols actually traded this run
    for symbol in symbols:
        try:
            tickers[symbol] = check_symbol(trading, data_client, symbol, shares, entry_dates)
        except Exception as e:
            print(f"[warn] {symbol} check failed: {e}")
            tickers[symbol] = {"error": str(e)}
            continue
        if tickers[symbol].get("last_order_id"):
            fired.append((symbol, tickers[symbol]["last_action"], tickers[symbol]["last_order_id"]))

    POSITION_STATE_PATH.write_text(json.dumps(entry_dates, indent=2))

    summary = "; ".join(text for _, text, _ in fired) if fired else f"checked {len(symbols)} tickers -- no setups"
    payload = {
        "connected": True,
        "account_type": "paper",
        "strategy": "breakout",
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "buying_power": buying_power,
        "last_action": summary,
        "last_order_id": fired[-1][2] if fired else None,
        "tickers": tickers,
    }
    STATUS_PATH.parent.mkdir(exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2))
    _report("/api/report_status", payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS,
                         help=f"Tickers to watch (default: the 8 validated ones -- {' '.join(DEFAULT_SYMBOLS)})")
    parser.add_argument("--shares", type=int, default=Breakout.params.size, help="Flat share count per trade, per symbol")
    args = parser.parse_args()

    run(args.symbols, args.shares)

"""
Runs a validated strategy live against Alpaca's PAPER trading account. Multi-strategy
workflow (2026-09-06, explicit user request -- "adding to him another agent... scale it to a
workflow"): one script, `--strategy breakout|rsi` picks which validated agent runs, each with
its OWN ticker universe, position state, and status file -- not a duplicated script per
strategy.

Ticker universes are DELIBERATELY DISJOINT between strategies. Alpaca tracks one position per
symbol per account, not per-strategy -- if two agents both tried to trade the same ticker,
their entry-date tracking and exit logic would collide over a position neither fully owns.
Breakout: SPY/QQQ/AAPL/MSFT/TSLA/JPM/XOM/IWM (originally validated) plus AMZN/META/NFLX/AMD/
BAC/HD/COST/CAT (added via expand_universe.py, 2026-09-05). RSI: GOOGL/V/MA/GS/WMT/DIS/PG/BA/F
-- the 9 (of 15 candidates NOT already claimed by Breakout) that scored positive Sharpe on
BOTH the TRAIN and TEST windows with RSI's own validated params (rsi_period=14, oversold=30,
exit_rsi=50 -- tuned, Sharpe 0.543 vs 0.498 default on unseen data; real but modest, nowhere
near Breakout's 3.188).

Meant to be re-run on a schedule (cron/Task Scheduler), once per trading day after the close --
NOT left running as a daemon at this stage. Each run is a fresh process, so exit logic needs
position state to survive between runs: Alpaca's own position record is the source of truth for
entry price (avg_entry_price) and quantity per symbol, but Alpaca doesn't track WHEN a position
was opened, so entry date per symbol is persisted locally, one small JSON file PER STRATEGY
(agent_position_state_<strategy>.json) so the two agents' state can never cross-contaminate.

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

# See module docstring for how each list was chosen and why they're disjoint.
BREAKOUT_SYMBOLS = [
    "SPY", "QQQ", "AAPL", "MSFT", "TSLA", "JPM", "XOM", "IWM",
    "AMZN", "META", "NFLX", "AMD", "BAC", "HD", "COST", "CAT",
]
RSI_SYMBOLS = ["GOOGL", "V", "MA", "GS", "WMT", "DIS", "PG", "BA", "F"]
RSI_PARAMS = {"rsi_period": 14, "oversold": 30, "exit_rsi": 50, "max_hold_days": 10}

STRATEGIES = {
    "breakout": {"symbols": BREAKOUT_SYMBOLS, "shares": Breakout.params.size},
    "rsi": {"symbols": RSI_SYMBOLS, "shares": 10},
}

WEBAPP_DIR = pathlib.Path(__file__).parent / "webapp"
# Trades stay in ONE shared file across every strategy (each row tagged "strategy") -- the
# dashboard's Live Trades table reads them all as one real fill history. Status and position
# state are PER STRATEGY -- see module docstring for why cross-agent state must stay separate.
TRADES_PATH = WEBAPP_DIR / "agent_trades.json"


def status_path(strategy: str) -> pathlib.Path:
    return WEBAPP_DIR / f"agent_status_{strategy}.json"


def position_state_path(strategy: str) -> pathlib.Path:
    return WEBAPP_DIR / f"agent_position_state_{strategy}.json"


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


def log_live_trade(strategy, symbol, side, price, size, reason):
    """Appends one real fill to the SHARED agent_trades.json -- the dashboard's 'Live
    trades' panel. Tagged with strategy now that more than one agent can trade."""
    trade = {
        "date": datetime.date.today().isoformat(),
        "strategy": strategy,
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
        title=f"[{strategy}] {verb} {symbol}",
        message=f"{size} shares @ ${price:.2f} -- {reason}",
        tags="moneybag" if side == "BUY" else "chart_with_downwards_trend",
    )


def load_entry_dates(strategy: str) -> dict:
    path = position_state_path(strategy)
    if path.exists():
        return json.loads(path.read_text())
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


def fetch_bars(data_client, symbol: str, lookback_days: int):
    bars_request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.datetime.now() - datetime.timedelta(days=lookback_days),
    )
    return data_client.get_stock_bars(bars_request).df


def submit_entry(trading, symbol, shares, strategy, side_reason) -> dict | None:
    """Shared entry-order guard: skip if an order for this symbol is already pending
    (2026-09-05 real incident -- a market order placed while the market's closed sits
    ACCEPTED for hours; re-running before it fills would otherwise submit a duplicate)."""
    open_orders = trading.get_orders(GetOrdersRequest(symbols=[symbol], status=QueryOrderStatus.OPEN))
    if open_orders:
        print(f"{symbol}: entry confirmed but an order is already pending (id {open_orders[0].id}) -- not submitting another.")
        return None
    order = MarketOrderRequest(symbol=symbol, qty=shares, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
    submitted = trading.submit_order(order)
    print(f"Submitted BUY {shares} {symbol} -- order id {submitted.id}")
    return {"order_id": str(submitted.id)}


def submit_exit(trading, symbol, qty, strategy) -> dict | None:
    open_orders = trading.get_orders(GetOrdersRequest(symbols=[symbol], status=QueryOrderStatus.OPEN))
    if open_orders:
        print(f"{symbol}: exit condition met but an order is already pending (id {open_orders[0].id}) -- not submitting another.")
        return None
    order = MarketOrderRequest(symbol=symbol, qty=abs(qty), side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
    submitted = trading.submit_order(order)
    print(f"Submitted SELL {abs(qty)} {symbol} -- order id {submitted.id}")
    return {"order_id": str(submitted.id)}


def check_breakout_symbol(trading, data_client, symbol: str, shares: int, entry_dates: dict) -> dict:
    p = Breakout.params
    lookback_days = max(p.trend_period, p.breakout_period) * 2  # generous buffer for weekends/holidays
    bars = fetch_bars(data_client, symbol, lookback_days)
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

        result = submit_entry(trading, symbol, shares, "breakout", "trend breakout")
        if result is None:
            return {"position": 0, "close": round(today_close, 2), "trend_ma": round(trend_ma, 2),
                    "highest": round(highest, 2), "last_action": "breakout confirmed, order already pending"}
        entry_dates[symbol] = datetime.date.today().isoformat()
        log_live_trade("breakout", symbol, "BUY", today_close, shares, "trend breakout")
        return {"position": shares, "close": round(today_close, 2), "trend_ma": round(trend_ma, 2),
                "highest": round(highest, 2), "last_action": f"BUY {shares} {symbol}",
                "last_order_id": result["order_id"]}

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
    result = submit_exit(trading, symbol, current_qty, "breakout")
    if result is None:
        return {"position": current_qty, "close": round(today_close, 2), "entry_price": round(avg_entry_price, 2),
                "last_action": f"exit ({reason}) pending, order already in flight"}
    entry_dates.pop(symbol, None)
    log_live_trade("breakout", symbol, "SELL", today_close, abs(current_qty), reason)
    return {"position": 0, "close": round(today_close, 2),
            "last_action": f"SELL {abs(current_qty)} {symbol} ({reason})", "last_order_id": result["order_id"]}


def _compute_rsi(closes) -> float:
    """Wilder's RSI via EMA(alpha=1/period) -- converges to the true Wilder smoothing after
    enough history (the generous lookback below gives it that), same formula
    bt.indicators.RSI uses for backtesting, so the live check matches what was validated."""
    period = RSI_PARAMS["rsi_period"]
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def check_rsi_symbol(trading, data_client, symbol: str, shares: int, entry_dates: dict) -> dict:
    period = RSI_PARAMS["rsi_period"]
    lookback_days = period * 6  # generous buffer for Wilder smoothing to converge + weekends/holidays
    bars = fetch_bars(data_client, symbol, lookback_days)
    if bars is None or len(bars) < period + 1:
        return {"error": f"not enough bars to compute {period}-period RSI yet"}

    closes = bars["close"]
    today_close = float(closes.iloc[-1])
    rsi_value = round(_compute_rsi(closes), 2)

    try:
        position = trading.get_open_position(symbol)
        current_qty = int(float(position.qty))
        avg_entry_price = float(position.avg_entry_price)
    except Exception:
        current_qty = 0
        avg_entry_price = None

    print(f"{datetime.date.today().isoformat()} {symbol} close={today_close:.2f} rsi={rsi_value} position={current_qty}")

    if current_qty == 0:
        entry_dates.pop(symbol, None)
        if rsi_value >= RSI_PARAMS["oversold"]:
            print(f"{symbol}: no entry -- RSI {rsi_value} not oversold. Holding flat.")
            return {"position": 0, "close": round(today_close, 2), "rsi": rsi_value,
                    "last_action": f"holding flat (RSI {rsi_value}, not oversold)"}

        result = submit_entry(trading, symbol, shares, "rsi", "oversold bounce")
        if result is None:
            return {"position": 0, "close": round(today_close, 2), "rsi": rsi_value,
                    "last_action": "oversold confirmed, order already pending"}
        entry_dates[symbol] = datetime.date.today().isoformat()
        log_live_trade("rsi", symbol, "BUY", today_close, shares, "oversold bounce")
        return {"position": shares, "close": round(today_close, 2), "rsi": rsi_value,
                "last_action": f"BUY {shares} {symbol}", "last_order_id": result["order_id"]}

    # In a position -- RSIReversion has NO stop-loss, only RSI-recovered or max_hold_days
    # (see strategies/rsi_reversion.py) -- matching that exactly here, not adding a stop the
    # validated strategy never had.
    entry_date_str = entry_dates.get(symbol)
    bars_held = (datetime.date.today() - datetime.date.fromisoformat(entry_date_str)).days if entry_date_str else None
    recovered = rsi_value > RSI_PARAMS["exit_rsi"]
    timed_out = bars_held is not None and bars_held >= RSI_PARAMS["max_hold_days"]

    if not (recovered or timed_out):
        print(f"{symbol}: holding {current_qty} @ entry {avg_entry_price:.2f}, RSI {rsi_value} -- no exit condition met.")
        return {"position": current_qty, "close": round(today_close, 2), "rsi": rsi_value,
                "entry_price": round(avg_entry_price, 2), "last_action": "holding position"}

    reason = "RSI recovered" if recovered else "max hold days"
    result = submit_exit(trading, symbol, current_qty, "rsi")
    if result is None:
        return {"position": current_qty, "close": round(today_close, 2), "rsi": rsi_value,
                "entry_price": round(avg_entry_price, 2), "last_action": f"exit ({reason}) pending, order already in flight"}
    entry_dates.pop(symbol, None)
    log_live_trade("rsi", symbol, "SELL", today_close, abs(current_qty), reason)
    return {"position": 0, "close": round(today_close, 2), "rsi": rsi_value,
            "last_action": f"SELL {abs(current_qty)} {symbol} ({reason})", "last_order_id": result["order_id"]}


CHECK_FUNCTIONS = {"breakout": check_breakout_symbol, "rsi": check_rsi_symbol}


def run(strategy: str, symbols: list[str], shares: int):
    trading, data_client = get_clients()
    check_fn = CHECK_FUNCTIONS[strategy]

    account = trading.get_account()
    buying_power = round(float(account.buying_power), 2)
    print(f"[{strategy}] Connected to Alpaca paper account -- buying power ${buying_power:,.2f}")

    entry_dates = load_entry_dates(strategy)
    tickers = {}
    fired = []  # (symbol, action_text, order_id) for whichever symbols actually traded this run
    for symbol in symbols:
        try:
            tickers[symbol] = check_fn(trading, data_client, symbol, shares, entry_dates)
        except Exception as e:
            print(f"[warn] {symbol} check failed: {e}")
            tickers[symbol] = {"error": str(e)}
            continue
        if tickers[symbol].get("last_order_id"):
            fired.append((symbol, tickers[symbol]["last_action"], tickers[symbol]["last_order_id"]))

    position_state_path(strategy).write_text(json.dumps(entry_dates, indent=2))

    summary = "; ".join(text for _, text, _ in fired) if fired else f"checked {len(symbols)} tickers -- no setups"
    payload = {
        "connected": True,
        "account_type": "paper",
        "strategy": strategy,
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "buying_power": buying_power,
        "last_action": summary,
        "last_order_id": fired[-1][2] if fired else None,
        "tickers": tickers,
    }
    status_path(strategy).parent.mkdir(exist_ok=True)
    status_path(strategy).write_text(json.dumps(payload, indent=2))
    _report(f"/api/report_status/{strategy}", payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=list(STRATEGIES), default="breakout")
    parser.add_argument("--symbols", nargs="+", default=None,
                         help="Override the strategy's default ticker list (see module docstring for the validated defaults)")
    parser.add_argument("--shares", type=int, default=None, help="Override the strategy's default flat share count per trade")
    args = parser.parse_args()

    defaults = STRATEGIES[args.strategy]
    run(args.strategy, args.symbols or defaults["symbols"], args.shares or defaults["shares"])

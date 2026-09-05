"""
Runs the validated Breakout strategy live against Alpaca's PAPER trading account -- the
strategy compare_strategies.py/tune_strategy.py's walk-forward testing actually confirmed
(Sharpe 3.188, 61.6% win rate on unseen data), not the original MACrossover plumbing-check
version this file used before 2026-09-05.

Meant to be re-run on a schedule (cron/Task Scheduler), once per trading day after the
close -- NOT left running as a daemon at this stage, same design choice as the original
version. Each run is a fresh process, so exit logic (stop-loss/target/max_hold) needs
position state to survive between runs: Alpaca's own position record is the source of truth
for entry price (avg_entry_price) and quantity, but Alpaca doesn't track WHEN a position was
opened, so entry date alone is persisted locally in agent_position_state.json.

SETUP: see README.md's Paper trading section for getting a free Alpaca paper key pair.
SAFETY: `paper=True` is hardcoded below, not a flag -- this script has no code path that can
submit a live order.
"""

import argparse
import datetime
import json
import pathlib
import os

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from strategies.breakout import Breakout

WEBAPP_DIR = pathlib.Path(__file__).parent / "webapp"
STATUS_PATH = WEBAPP_DIR / "agent_status.json"
TRADES_PATH = WEBAPP_DIR / "agent_trades.json"
# Just the entry date -- everything else about an open position (qty, avg fill price) is
# read straight from Alpaca each run rather than duplicated here, so this file can never
# drift out of sync with what the broker actually thinks we hold.
POSITION_STATE_PATH = WEBAPP_DIR / "agent_position_state.json"


def write_status(**fields):
    """Lets the dashboard's 'agent connected' badge mean something real -- see
    webapp/main.py's own /api/status, which just reports connected=false until this file
    exists."""
    STATUS_PATH.parent.mkdir(exist_ok=True)
    payload = {
        "connected": True,
        "account_type": "paper",
        "strategy": "breakout",
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    payload.update(fields)
    STATUS_PATH.write_text(json.dumps(payload, indent=2))


def log_live_trade(side, price, size, reason):
    """Appends one real fill to agent_trades.json -- the dashboard's 'Live trades' panel,
    kept entirely separate from a backtest's trade_log so the two are never confused for
    each other."""
    trades = json.loads(TRADES_PATH.read_text()) if TRADES_PATH.exists() else []
    trades.append({
        "date": datetime.date.today().isoformat(),
        "side": side,
        "price": round(price, 2),
        "size": size,
        "reason": reason,
    })
    TRADES_PATH.write_text(json.dumps(trades, indent=2))


def load_entry_date():
    if POSITION_STATE_PATH.exists():
        return json.loads(POSITION_STATE_PATH.read_text()).get("entry_date")
    return None


def save_entry_date(entry_date: str):
    POSITION_STATE_PATH.write_text(json.dumps({"entry_date": entry_date}))


def clear_entry_date():
    if POSITION_STATE_PATH.exists():
        POSITION_STATE_PATH.unlink()


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


def run(symbol: str, shares: int):
    trading, data_client = get_clients()
    p = Breakout.params

    account = trading.get_account()
    print(f"Connected to Alpaca paper account -- buying power ${float(account.buying_power):,.2f}")

    lookback_days = max(p.trend_period, p.breakout_period) * 2  # generous buffer for weekends/holidays
    bars_request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.datetime.now() - datetime.timedelta(days=lookback_days),
    )
    bars = data_client.get_stock_bars(bars_request).df
    if bars is None or len(bars) < p.trend_period + 1:
        raise SystemExit(f"Not enough historical bars returned to compute the {p.trend_period}-day trend yet.")

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
        clear_entry_date()  # flat -- any stale entry_date from a prior run no longer applies
        in_uptrend = today_close > trend_ma
        broke_out = today_close > highest
        if not (in_uptrend and broke_out):
            reason = "no breakout" if not broke_out else "below trend"
            print(f"No entry -- {reason}. Holding flat.")
            write_status(symbol=symbol, position=0, close=round(today_close, 2),
                         trend_ma=round(trend_ma, 2), highest=round(highest, 2),
                         last_action=f"holding flat ({reason})")
            return

        order = MarketOrderRequest(symbol=symbol, qty=shares, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        submitted = trading.submit_order(order)
        save_entry_date(datetime.date.today().isoformat())
        log_live_trade("BUY", today_close, shares, "trend breakout")
        print(f"Submitted BUY {shares} {symbol} -- order id {submitted.id}")
        write_status(symbol=symbol, position=shares, close=round(today_close, 2),
                     trend_ma=round(trend_ma, 2), highest=round(highest, 2),
                     last_action=f"BUY {shares} {symbol}", last_order_id=str(submitted.id))
        return

    # In a position -- check exits. avg_entry_price comes straight from Alpaca (the real
    # fill price), not something this script tracked itself.
    entry_date_str = load_entry_date()
    bars_held = (datetime.date.today() - datetime.date.fromisoformat(entry_date_str)).days if entry_date_str else None

    stopped_out = today_close <= avg_entry_price * (1 - p.stop_pct)
    hit_target = today_close >= avg_entry_price * (1 + p.target_pct)
    timed_out = bars_held is not None and bars_held >= p.max_hold_days

    if not (stopped_out or hit_target or timed_out):
        print(f"Holding {current_qty} {symbol} @ entry {avg_entry_price:.2f} -- no exit condition met.")
        write_status(symbol=symbol, position=current_qty, close=round(today_close, 2),
                     entry_price=round(avg_entry_price, 2), last_action="holding position")
        return

    reason = "stop-loss" if stopped_out else "profit target" if hit_target else "max hold days"
    order = MarketOrderRequest(symbol=symbol, qty=abs(current_qty), side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
    submitted = trading.submit_order(order)
    clear_entry_date()
    log_live_trade("SELL", today_close, abs(current_qty), reason)
    print(f"Submitted SELL {abs(current_qty)} {symbol} -- order id {submitted.id} ({reason})")
    write_status(symbol=symbol, position=0, close=round(today_close, 2),
                 last_action=f"SELL {abs(current_qty)} {symbol} ({reason})", last_order_id=str(submitted.id))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY", help="Stock ticker to trade -- any of the 8 validated in tune_strategy.py's default set")
    parser.add_argument("--shares", type=int, default=Breakout.params.size, help="Flat share count per trade")
    args = parser.parse_args()

    run(args.symbol, args.shares)

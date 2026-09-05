"""
Runs MACrossover live against Alpaca's PAPER trading account -- same strategy class the
backtester uses. Alpaca instead of IBKR for the stock version: no desktop Gateway app to
run, just a REST API and a free paper account from signup alone (no ID upload needed until
you want real money later).

SETUP (one-time):
  1. Sign up free at https://alpaca.markets -- paper trading is available immediately,
     before any identity verification.
  2. Dashboard -> API Keys -> generate a PAPER key pair (there are separate keys for
     paper vs live -- make sure you copy the paper ones).
  3. Set them as environment variables (don't hardcode secrets into this file):
       setx ALPACA_API_KEY "your-key-id"        (Windows, then open a new terminal)
       setx ALPACA_SECRET_KEY "your-secret-key"

SAFETY: `paper=True` is hardcoded below, not a flag -- this script has no code path that can
submit a live order. Going live later means writing that deliberately, not flipping a switch
on this file.
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

from strategies.ma_crossover import MACrossover

STATUS_PATH = pathlib.Path(__file__).parent / "webapp" / "agent_status.json"


def write_status(**fields):
    """Lets the dashboard's 'agent connected' badge mean something real -- see webapp/main.
    py's own /api/status, which just reports available=false until this file exists."""
    STATUS_PATH.parent.mkdir(exist_ok=True)
    payload = {"connected": True, "account_type": "paper", "updated_at": datetime.datetime.now().isoformat(timespec="seconds")}
    payload.update(fields)
    STATUS_PATH.write_text(json.dumps(payload, indent=2))


def get_clients() -> tuple[TradingClient, StockHistoricalDataClient]:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit(
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables first -- "
            "see this script's module docstring for where to get them."
        )
    trading = TradingClient(api_key, secret_key, paper=True)  # hardcoded -- see SAFETY note above
    data = StockHistoricalDataClient(api_key, secret_key)
    return trading, data


def run(symbol: str, allow_short: bool):
    trading, data_client = get_clients()

    account = trading.get_account()
    print(f"Connected to Alpaca paper account -- buying power ${float(account.buying_power):,.2f}")
    write_status(symbol=symbol, buying_power=float(account.buying_power), last_action="checking")

    bars_request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.datetime.now() - datetime.timedelta(days=90),
    )
    bars = data_client.get_stock_bars(bars_request).df
    if bars is None or len(bars) < MACrossover.params.slow_period + 1:
        raise SystemExit("Not enough historical bars returned to compute the slow moving average yet.")

    closes = bars["close"]
    fast = closes.rolling(MACrossover.params.fast_period).mean()
    slow = closes.rolling(MACrossover.params.slow_period).mean()
    crossed_up = fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]
    crossed_down = fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]

    try:
        position = trading.get_open_position(symbol)
        current_qty = int(float(position.qty)) * (1 if position.side == "long" else -1)
    except Exception:
        current_qty = 0  # no open position in this symbol

    print(f"{datetime.datetime.now().isoformat(timespec='seconds')} "
          f"{symbol} fast={fast.iloc[-1]:.2f} slow={slow.iloc[-1]:.2f} position={current_qty}")
    write_status(symbol=symbol, position=current_qty, fast=round(float(fast.iloc[-1]), 2),
                 slow=round(float(slow.iloc[-1]), 2), last_action="checking")

    # Same target-position logic as backtest.py's MACrossover.next() and the IBKR version
    # in paper_trade.py -- one order for whatever delta gets from wherever we are to target.
    shares = MACrossover.params.size
    if crossed_up:
        target = shares
    elif crossed_down:
        target = -shares if allow_short else 0
    else:
        print("No crossover -- holding.")
        write_status(symbol=symbol, position=current_qty, last_action="holding (no crossover)")
        return

    delta = target - current_qty
    if delta == 0:
        print("Already at target position -- holding.")
        write_status(symbol=symbol, position=current_qty, last_action="holding (already at target)")
        return

    order = MarketOrderRequest(
        symbol=symbol,
        qty=abs(delta),
        side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    submitted = trading.submit_order(order)
    print(f"Submitted {order.side.value.upper()} {abs(delta)} {symbol} -- order id {submitted.id}")
    write_status(symbol=symbol, position=target, last_action=f"{order.side.value.upper()} {abs(delta)} {symbol}",
                 last_order_id=str(submitted.id))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY", help="Stock ticker to trade, e.g. SPY, AAPL")
    parser.add_argument("--allow-short", action="store_true", help="Flip short on a bearish crossover instead of just going flat")
    args = parser.parse_args()

    run(args.symbol, args.allow_short)

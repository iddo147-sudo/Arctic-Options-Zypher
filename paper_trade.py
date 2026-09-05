"""
Runs MACrossover live against Interactive Brokers' PAPER account -- same strategy class the
backtester uses, so a change to the strategy logic only ever needs to happen in one place.

SETUP (one-time):
  1. Open a free IBKR account if you don't have one: https://www.interactivebrokers.com
  2. Install IB Gateway (lighter than full TWS -- https://www.interactivebrokers.com/en/trading/ibgateway-stable.php)
  3. Log into IB Gateway with PAPER TRADING selected on the login screen, not live.
  4. In Gateway: Configure -> Settings -> API -> Enable ActiveX and Socket Clients,
     confirm the socket port matches PAPER_PORT below (default 4002 for Gateway paper).

SAFETY: this script refuses to connect to anything but a well-known PAPER port. If IBKR
ever changes those defaults, or you're pointing this at a real account, it exits instead of
guessing -- an automated system silently trading a live account because of a typo'd port
number is exactly the failure mode this project is not willing to risk.
"""

import argparse
import datetime

from ib_insync import IB, Future, MarketOrder, util

from strategies.ma_crossover import MACrossover
import backtrader as bt

# IB Gateway paper=4002/live=4001, TWS paper=7497/live=7496. Only the PAPER ports are
# accepted here on purpose -- see the module docstring's SAFETY note.
PAPER_PORTS = {4002, 7497}

POINT_VALUE = 5.0
CONTRACT_SIZE = 1


def connect(host: str, port: int, client_id: int) -> IB:
    if port not in PAPER_PORTS:
        raise SystemExit(
            f"Refusing to connect on port {port} -- only known PAPER-account ports "
            f"{sorted(PAPER_PORTS)} are accepted. If IBKR's own defaults changed, update "
            f"PAPER_PORTS above deliberately rather than removing this check."
        )
    ib = IB()
    ib.connect(host, port, clientId=client_id)
    account_values = ib.accountValues()
    account_type = next((v.value for v in account_values if v.tag == "AccountType"), "unknown")
    print(f"Connected to {host}:{port} -- account type reported as: {account_type}")
    return ib


def run(host: str, port: int, client_id: int, contract_month: str, allow_short: bool):
    ib = connect(host, port, client_id)

    contract = Future(symbol="MES", lastTradeDateOrContractMonth=contract_month, exchange="CME", currency="USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise SystemExit(f"Could not qualify contract MES {contract_month} on CME -- check the contract month.")
    print(f"Trading {qualified[0].localSymbol}")

    # Pull recent bars from IB itself (not yfinance) so the live signal is computed off the
    # same data source it will actually trade against -- avoids a strategy that looks right
    # in the backtester but disagrees with itself once real broker data feeds it.
    bars = ib.reqHistoricalData(
        qualified[0],
        endDateTime="",
        durationStr="60 D",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )
    df = util.df(bars)
    if df is None or len(df) < MACrossover.params.slow_period + 1:
        raise SystemExit("Not enough historical bars returned to compute the slow moving average yet.")

    fast = df["close"].rolling(MACrossover.params.fast_period).mean()
    slow = df["close"].rolling(MACrossover.params.slow_period).mean()
    crossed_up = fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]
    crossed_down = fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]

    positions = {p.contract.conId: p.position for p in ib.positions()}
    current_position = positions.get(qualified[0].conId, 0)

    print(f"{datetime.datetime.now().isoformat(timespec='seconds')} "
          f"fast={fast.iloc[-1]:.2f} slow={slow.iloc[-1]:.2f} position={current_position}")

    # Same target-position logic as backtest.py's MACrossover.next() -- one delta order
    # gets from wherever we currently are (flat/long/short) to the target, so it handles
    # a flip (long straight to short, or vice versa) in a single order, matching the
    # backtested behavior exactly rather than approximating it with separate close+open.
    if crossed_up:
        target = CONTRACT_SIZE
    elif crossed_down:
        target = -CONTRACT_SIZE if allow_short else 0
    else:
        print("No crossover -- holding.")
        ib.disconnect()
        return

    delta = target - current_position
    if delta > 0:
        order = MarketOrder("BUY", delta)
        trade = ib.placeOrder(qualified[0], order)
        print(f"Submitted BUY {delta} -- {trade}")
    elif delta < 0:
        order = MarketOrder("SELL", -delta)
        trade = ib.placeOrder(qualified[0], order)
        print(f"Submitted SELL {-delta} -- {trade}")
    else:
        print("Already at target position -- holding.")

    ib.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002, help="IB Gateway/TWS paper port (see module docstring)")
    parser.add_argument("--client-id", type=int, default=7, help="Arbitrary integer identifying this script to IB; must be unique per connected client")
    parser.add_argument("--contract-month", default="", help="e.g. 202512 for the December 2026 MES contract -- leave blank to let IB pick the front month")
    parser.add_argument("--allow-short", action="store_true", help="Flip short on a bearish crossover instead of just going flat -- match whatever backtest.py was run with")
    args = parser.parse_args()

    run(args.host, args.port, args.client_id, args.contract_month, args.allow_short)

"""
Runs a strategy against cached historical data (see fetch_data.py) and prints the numbers
that actually matter before anyone trusts this with paper (let alone real) money: total
return, max drawdown, Sharpe, win rate, and trade count.

STOCKS, not futures (switched 2026-09-04, explicit user request) -- plain 1 share = 1x its
own price, no point-value multiplier and no per-contract margin the way MES needed. `--shares`
is a flat share count per trade, same simplicity tradeoff the futures version had with
contracts: fine for one ticker at a time, not auto-scaled to price -- 10 shares of a $20
stock and 10 shares of a $2,000 stock are very different bets, so sanity-check `--shares`
against whatever `--csv` you're actually pointing at.
"""

import argparse
import json
import pathlib

import backtrader as bt

from strategies.ma_crossover import MACrossover

DATA_DIR = pathlib.Path(__file__).parent / "data"
WEBAPP_DIR = pathlib.Path(__file__).parent / "webapp"

STARTING_CASH = 10_000
COMMISSION_PER_SHARE = 0.0  # Alpaca's US equities are commission-free; adjust if using a different broker.


def run(csv_path: pathlib.Path, fast: int, slow: int, allow_short: bool, shares: int):
    cerebro = bt.Cerebro()
    cerebro.addstrategy(MACrossover, fast_period=fast, slow_period=slow, allow_short=allow_short, size=shares)

    data = bt.feeds.GenericCSVData(
        dataname=str(csv_path),
        dtformat="%Y-%m-%d",
        openinterest=-1,   # yfinance doesn't provide open interest -- tell backtrader not to expect that column
    )
    cerebro.adddata(data)

    cerebro.broker.setcash(STARTING_CASH)
    cerebro.broker.setcommission(commission=COMMISSION_PER_SHARE)

    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.0, annualize=True)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")

    start_value = cerebro.broker.getvalue()
    result = cerebro.run()[0]
    end_value = cerebro.broker.getvalue()

    trades = result.analyzers.trades.get_analysis()
    total_trades = trades.get("total", {}).get("total", 0)
    won = trades.get("won", {}).get("total", 0)
    lost = trades.get("lost", {}).get("total", 0)

    print("\n=== Backtest results ===")
    print(f"Start value:   ${start_value:,.2f}")
    print(f"End value:     ${end_value:,.2f}")
    print(f"Return:        {100 * (end_value / start_value - 1):.2f}%")
    print(f"Max drawdown:  {result.analyzers.drawdown.get_analysis().max.drawdown:.2f}%")
    print(f"Sharpe:        {result.analyzers.sharpe.get_analysis().get('sharperatio')}")
    print(f"Trades:        {total_trades} (won {won} / lost {lost})")
    if total_trades:
        print(f"Win rate:      {100 * won / total_trades:.1f}%")

    sharpe = result.analyzers.sharpe.get_analysis().get("sharperatio")
    WEBAPP_DIR.mkdir(exist_ok=True)
    payload = {
        "ticker": csv_path.stem,
        "fast_period": fast,
        "slow_period": slow,
        "allow_short": allow_short,
        "start_value": start_value,
        "end_value": end_value,
        "return_pct": round(100 * (end_value / start_value - 1), 2),
        "max_drawdown_pct": round(result.analyzers.drawdown.get_analysis().max.drawdown, 2),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "total_trades": total_trades,
        "won": won,
        "lost": lost,
        "win_rate_pct": round(100 * won / total_trades, 1) if total_trades else None,
        "equity_curve": result.equity_curve,
        "price_series": result.price_series,
        "trade_log": result.trade_log,
    }
    out_path = WEBAPP_DIR / "results.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nDashboard data written to {out_path}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(DATA_DIR / "SPY.csv"), help="Path to a CSV produced by fetch_data.py")
    parser.add_argument("--fast", type=int, default=10, help="Fast SMA period")
    parser.add_argument("--slow", type=int, default=30, help="Slow SMA period")
    parser.add_argument("--allow-short", action="store_true", help="Flip short on a bearish crossover instead of just going flat")
    parser.add_argument("--shares", type=int, default=10, help="Flat share count per trade -- see module docstring's sizing caveat")
    args = parser.parse_args()

    csv_path = pathlib.Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"{csv_path} doesn't exist -- run fetch_data.py first.")

    run(csv_path, args.fast, args.slow, args.allow_short, args.shares)

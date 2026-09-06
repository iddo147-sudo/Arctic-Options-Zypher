"""
Runs a strategy against cached historical data (see fetch_data.py) and prints the numbers
that actually matter before anyone trusts this with paper (let alone real) money: total
return, max drawdown, Sharpe, win rate, and trade count.

Four candidate strategies (--strategy): macrossover (the original plumbing-check strategy),
momentum, rsi (mean-reversion), breakout -- see strategies/*.py for what each one actually
does and the real research each is based on. None of them is "the" strategy; the point of
having four is comparing them against real data with --compare rather than trusting any one
of them on reputation.

STOCKS, not futures (switched 2026-09-04) -- plain 1 share = 1x its own price, no point-value
multiplier and no per-contract margin the way MES needed. `--shares` is a flat share count
per trade: fine for one ticker at a time, not auto-scaled to price -- 10 shares of a $20
stock and 10 shares of a $2,000 stock are very different bets, so sanity-check `--shares`
against whatever `--csv` you're actually pointing at.
"""

import argparse
import json
import pathlib

import backtrader as bt

from strategies.ma_crossover import MACrossover
from strategies.momentum import Momentum
from strategies.rsi_reversion import RSIReversion
from strategies.breakout import Breakout
from strategies.breakdown import Breakdown
from strategies.relief_short import ReliefShort

DATA_DIR = pathlib.Path(__file__).parent / "data"
WEBAPP_DIR = pathlib.Path(__file__).parent / "webapp"

STARTING_CASH = 10_000
COMMISSION_PER_SHARE = 0.0  # Alpaca's US equities are commission-free; adjust if using a different broker.

STRATEGIES = {
    "macrossover": MACrossover,
    "momentum": Momentum,
    "rsi": RSIReversion,
    "breakout": Breakout,
    "breakdown": Breakdown,
    "relief_short": ReliefShort,
}


def run(csv_path: pathlib.Path, strategy_name: str, shares: int, allow_short: bool,
        extra_params: dict, quiet: bool = False):
    strategy_cls = STRATEGIES[strategy_name]

    cerebro = bt.Cerebro(stdstats=False)
    params = {"size": shares, **extra_params}
    # Not every strategy has an allow_short param (Breakout doesn't) -- only pass it to the
    # ones that declared it, rather than erroring on an unexpected kwarg.
    if "allow_short" in strategy_cls.params._getkeys():
        params["allow_short"] = allow_short
    cerebro.addstrategy(strategy_cls, **params)

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

    start_value = cerebro.broker.getvalue()
    result = cerebro.run()[0]
    end_value = cerebro.broker.getvalue()

    trades = result.analyzers.trades.get_analysis()
    total_trades = trades.get("total", {}).get("total", 0)
    won = trades.get("won", {}).get("total", 0)
    lost = trades.get("lost", {}).get("total", 0)
    sharpe = result.analyzers.sharpe.get_analysis().get("sharperatio")

    summary = {
        "strategy": strategy_name,
        "ticker": csv_path.stem,
        # The FULL effective params (defaults + overrides), not just whatever was passed in
        # -- `params` above only has overrides, which is empty for a plain default run and
        # made the dashboard's footer show nothing (or, before that, a hardcoded MA-only
        # field showing "null/null" for every non-MACrossover strategy).
        "params": {k: getattr(result.params, k) for k in result.params._getkeys()},
        "start_value": start_value,
        "end_value": end_value,
        "return_pct": round(100 * (end_value / start_value - 1), 2),
        "max_drawdown_pct": round(result.analyzers.drawdown.get_analysis().max.drawdown, 2),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "total_trades": total_trades,
        "won": won,
        "lost": lost,
        "win_rate_pct": round(100 * won / total_trades, 1) if total_trades else None,
    }

    if not quiet:
        print(f"\n=== {strategy_name} on {csv_path.stem} ===")
        print(f"Start value:   ${start_value:,.2f}")
        print(f"End value:     ${end_value:,.2f}")
        print(f"Return:        {summary['return_pct']:.2f}%")
        print(f"Max drawdown:  {summary['max_drawdown_pct']:.2f}%")
        print(f"Sharpe:        {summary['sharpe']}")
        print(f"Trades:        {total_trades} (won {won} / lost {lost})")
        if total_trades:
            print(f"Win rate:      {summary['win_rate_pct']:.1f}%")

    return result, summary


def write_dashboard_payload(csv_path, strategy_name, params, summary, result):
    WEBAPP_DIR.mkdir(exist_ok=True)
    payload = {
        "ticker": csv_path.stem,
        "strategy": strategy_name,
        "fast_period": params.get("fast_period"),
        "slow_period": params.get("slow_period"),
        "allow_short": params.get("allow_short", False),
        **summary,
        "equity_curve": result.equity_curve,
        "price_series": result.price_series,
        "trade_log": result.trade_log,
    }
    out_path = WEBAPP_DIR / "results.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nDashboard data written to {out_path}")


def parse_params(pairs: list[str]) -> dict:
    """--param lookback=15 --param entry_threshold=0.05 -> {"lookback": 15, "entry_threshold": 0.05}.
    Values are parsed as int, then float, then left as a string -- covers every param type
    the four strategies currently use without needing a schema per strategy."""
    out = {}
    for pair in pairs:
        key, _, raw = pair.partition("=")
        for caster in (int, float):
            try:
                out[key] = caster(raw)
                break
            except ValueError:
                continue
        else:
            out[key] = raw
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(DATA_DIR / "SPY_2y.csv"), help="Path to a CSV produced by fetch_data.py")
    parser.add_argument("--strategy", choices=list(STRATEGIES), default="macrossover")
    parser.add_argument("--shares", type=int, default=10, help="Flat share count per trade -- see module docstring's sizing caveat")
    parser.add_argument("--allow-short", action="store_true", help="Where supported: take the opposite-direction trade too, not just exit to flat")
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                         help="Override any strategy-specific param, e.g. --param lookback=15 --param rsi_period=7. Repeatable.")
    parser.add_argument("--compare", action="store_true",
                         help="Run all four strategies on the same --csv with their own defaults and print a comparison table instead of one detailed run")
    args = parser.parse_args()

    csv_path = pathlib.Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"{csv_path} doesn't exist -- run fetch_data.py first.")

    if args.compare:
        rows = []
        for name in STRATEGIES:
            _, summary = run(csv_path, name, args.shares, args.allow_short, {}, quiet=True)
            rows.append(summary)
        print(f"\n=== Strategy comparison on {csv_path.stem} ===")
        header = f"{'Strategy':<12} {'Return':>8} {'MaxDD':>8} {'Sharpe':>8} {'Trades':>7} {'WinRate':>8}"
        print(header)
        print("-" * len(header))
        for s in rows:
            print(f"{s['strategy']:<12} {s['return_pct']:>7.2f}% {s['max_drawdown_pct']:>7.2f}% "
                  f"{(s['sharpe'] if s['sharpe'] is not None else float('nan')):>8.3f} {s['total_trades']:>7} "
                  f"{(s['win_rate_pct'] if s['win_rate_pct'] is not None else 0):>7.1f}%")
    else:
        extra_params = parse_params(args.param)
        result, summary = run(csv_path, args.strategy, args.shares, args.allow_short, extra_params)
        write_dashboard_payload(csv_path, args.strategy, {**extra_params, "allow_short": args.allow_short}, summary, result)

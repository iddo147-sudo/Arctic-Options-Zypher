"""
Real walk-forward test for ORB using Alpaca's own historical minute-bar data (free, already
authenticated -- same account running Breakout/RSI live) instead of Yahoo's 60-day intraday
cap. Same TRAIN/TEST discipline as everything else in this repo: TRAIN decides, TEST (never
touched during the decision) confirms or rejects.

Caveat worth remembering when reading results: Alpaca's free tier serves the IEX feed, not
the full consolidated SIP tape -- it only sees trades that happened on IEX, roughly 2-3% of
total US equity volume. Fine for spotting a strategy's shape on liquid names (SPY, QQQ,
mega-caps); noisier and less trustworthy the smaller/less-liquid the ticker gets.
"""

import argparse
import datetime
import pathlib
import statistics

import backtrader as bt
import pandas as pd

from strategies.orb import ORB
from strategies.vwap_reversion import VWAPReversion

DATA_DIR = pathlib.Path(__file__).parent / "data_intraday"
STARTING_CASH = 10_000
STRATEGIES = {"orb": ORB, "vwap": VWAPReversion}


def fetch_minute_bars(ticker: str, start: str, end: str) -> pathlib.Path:
    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / f"{ticker}_{start}_{end}_1min.csv"
    if out_path.exists():
        return out_path

    import os
    from dotenv import load_dotenv
    load_dotenv()
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    req = StockBarsRequest(
        symbol_or_symbols=[ticker],
        timeframe=TimeFrame.Minute,
        start=datetime.datetime.fromisoformat(start),
        end=datetime.datetime.fromisoformat(end),
    )
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        raise SystemExit(f"No bars returned for {ticker} {start}..{end}")
    df = df.reset_index(level="symbol", drop=True)
    df.to_csv(out_path)
    print(f"Saved {len(df)} rows to {out_path}")
    return out_path


def load_feed(csv_path: pathlib.Path) -> bt.feeds.PandasData:
    df = pd.read_csv(csv_path, index_col="timestamp", parse_dates=["timestamp"])
    # Alpaca timestamps are UTC -- convert to US/Eastern and drop the tz so backtrader's
    # datetime.time(0) comparisons in orb.py line up with market_open/market_close, which
    # are plain (tz-naive) 9:30/16:00 ET times.
    df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    return bt.feeds.PandasData(dataname=df, timeframe=bt.TimeFrame.Minutes)


def run_strategy(csv_path: pathlib.Path, strategy_name: str, ticker: str, shares: int, allow_short: bool,
                  extra_params: dict, quiet: bool = True):
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.addstrategy(STRATEGIES[strategy_name], size=shares, allow_short=allow_short, **extra_params)
    cerebro.adddata(load_feed(csv_path))
    cerebro.broker.setcash(STARTING_CASH)
    cerebro.broker.setcommission(commission=0.0)
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.0, annualize=True,
                         timeframe=bt.TimeFrame.Days)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    start_value = cerebro.broker.getvalue()
    result = cerebro.run()[0]
    end_value = cerebro.broker.getvalue()

    trades = result.analyzers.trades.get_analysis()
    total_trades = trades.get("total", {}).get("total", 0)
    won = trades.get("won", {}).get("total", 0)
    sharpe = result.analyzers.sharpe.get_analysis().get("sharperatio")

    df = pd.read_csv(csv_path)
    bh_pct = round(100 * (df["close"].iloc[-1] / df["close"].iloc[0] - 1), 2)

    summary = {
        "ticker": ticker,
        "return_pct": round(100 * (end_value / start_value - 1), 2),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "total_trades": total_trades,
        "win_rate_pct": round(100 * won / total_trades, 1) if total_trades else None,
        "buy_and_hold_pct": bh_pct,
    }
    if not quiet:
        print(summary)
    return summary


def main(strategy_name, tickers, train_start, train_end, test_start, test_end, shares, extra_params):
    print(f"Fetching + running {strategy_name} TRAIN window {train_start} to {train_end}...\n")
    train_results = []
    for t in tickers:
        csv_path = fetch_minute_bars(t, train_start, train_end)
        for allow_short in (False, True):
            s = run_strategy(csv_path, strategy_name, t, shares, allow_short, extra_params)
            s["allow_short"] = allow_short
            train_results.append(s)
            print(f"  TRAIN {t:6s} short={allow_short}  return={s['return_pct']}%  sharpe={s['sharpe']}  "
                  f"trades={s['total_trades']}  win_rate={s['win_rate_pct']}%  b&h={s['buy_and_hold_pct']}%")

    for direction_label, allow_short in (("long-only", False), ("long+short", True)):
        subset = [r for r in train_results if r["allow_short"] == allow_short and r["sharpe"] is not None]
        avg_sharpe = statistics.mean(r["sharpe"] for r in subset) if subset else float("-inf")
        print(f"\nTRAIN avg Sharpe, {direction_label}: {avg_sharpe:.3f}")

    long_only_sharpes = [r["sharpe"] for r in train_results if not r["allow_short"] and r["sharpe"] is not None]
    short_sharpes = [r["sharpe"] for r in train_results if r["allow_short"] and r["sharpe"] is not None]
    best_direction = "long+short" if short_sharpes and statistics.mean(short_sharpes) > statistics.mean(long_only_sharpes or [float("-inf")]) else "long-only"
    best_allow_short = best_direction == "long+short"
    print(f"\nDirection decided from TRAIN: {best_direction}")

    print(f"\n=== Evaluating on TEST window {test_start} to {test_end} (never seen during the decision) ===")
    test_results = []
    for t in tickers:
        csv_path = fetch_minute_bars(t, test_start, test_end)
        s = run_strategy(csv_path, strategy_name, t, shares, best_allow_short, extra_params)
        test_results.append(s)
        beat = "BEAT B&H" if s["return_pct"] > s["buy_and_hold_pct"] else "lost to B&H"
        print(f"  TEST {t:6s}  return={s['return_pct']}%  sharpe={s['sharpe']}  trades={s['total_trades']}  "
              f"win_rate={s['win_rate_pct']}%  b&h={s['buy_and_hold_pct']}%  [{beat}]")

    beat_count = sum(1 for r in test_results if r["return_pct"] > r["buy_and_hold_pct"])
    avg_return = statistics.mean(r["return_pct"] for r in test_results)
    valid_sharpes = [r["sharpe"] for r in test_results if r["sharpe"] is not None]
    avg_sharpe = statistics.mean(valid_sharpes) if valid_sharpes else float("nan")

    print(f"\n=== RESULT: avg return {avg_return:.2f}%, avg Sharpe {avg_sharpe:.3f}, "
          f"beat buy&hold {beat_count}/{len(test_results)} ===")
    if beat_count == 0 or avg_sharpe <= 0:
        print(f"Reject -- {strategy_name} does not clear the bar on real, unseen data either.")
    else:
        print("Passed the beat-buy&hold / positive-Sharpe bar on real data -- worth a closer look "
              "(still just one TEST window; treat like Breakout/RSI's first pass, not a final verdict).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=list(STRATEGIES), default="orb")
    parser.add_argument("--tickers", nargs="+", default=["SPY", "QQQ", "TSLA", "NVDA"])
    parser.add_argument("--train-start", default="2021-01-01")
    parser.add_argument("--train-end", default="2024-06-30")
    parser.add_argument("--test-start", default="2024-07-01")
    parser.add_argument("--test-end", default="2026-09-01")
    parser.add_argument("--shares", type=int, default=10)
    parser.add_argument("--or-minutes", type=int, default=15)
    args = parser.parse_args()
    extra = {"or_minutes": args.or_minutes} if args.strategy == "orb" else {}
    main(args.strategy, args.tickers, args.train_start, args.train_end, args.test_start, args.test_end,
         args.shares, extra)

"""
Pulls historical stock price data via yfinance and caches it to data/<ticker>.csv.

Switched from futures to stocks (2026-09-04, explicit user request). Yahoo's stock data is
adjusted-close by default handling; splits/dividends are accounted for automatically, unlike
the futures version's continuous-contract roll-splicing problem -- one less thing to
distrust in the backtest, though real broker fills in paper trading are still the only
actual validation.
"""

import argparse
import pathlib

import yfinance as yf

DATA_DIR = pathlib.Path(__file__).parent / "data"


def fetch(ticker: str, period: str, interval: str) -> pathlib.Path:
    df = yf.download(ticker, period=period, interval=interval, progress=False)
    if df.empty:
        raise SystemExit(f"No data returned for {ticker} -- check the ticker and period/interval combination.")

    # yfinance returns a MultiIndex column (Price, Ticker) when given one ticker as a
    # list-like; flatten it so backtrader's plain-CSV feed can read it without help.
    if isinstance(df.columns, __import__("pandas").MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # yfinance writes columns alphabetically (Close before Open) -- backtrader's
    # GenericCSVData defaults to standard OHLCV column order, so reorder here rather than
    # passing explicit column-index params at every single call site that reads this file.
    df = df[["Open", "High", "Low", "Close", "Volume"]]

    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / f"{ticker.replace('=', '_')}.csv"
    df.to_csv(out_path)
    print(f"Saved {len(df)} rows to {out_path}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", default="SPY", help="Yahoo Finance stock/ETF ticker, e.g. SPY, AAPL, TSLA, QQQ")
    parser.add_argument("--period", default="2y", help="How far back, e.g. 6mo, 1y, 2y, max")
    parser.add_argument("--interval", default="1d", help="Bar size, e.g. 1d, 1h, 15m (intraday history is limited to ~60 days by Yahoo)")
    args = parser.parse_args()
    fetch(args.ticker, args.period, args.interval)

# Swing trading bot -- paper only until proven

Started 2026-09-04 on futures/IBKR, switched to **stocks/Alpaca** the same night (explicit
user request -- Alpaca's paper signup needs no ID verification, unlike IBKR's). Goal: a
rule-based swing strategy, backtested, then run on a **paper** account for long enough to
trust it -- real money is explicitly not in scope until it's earned that with real (paper)
results.

## What's here

- `fetch_data.py` -- pulls historical stock/ETF bars from Yahoo Finance, caches to `data/`.
- `strategies/ma_crossover.py` -- the starting strategy (SMA crossover, `--allow-short` for
  long+short). Not expected to be profitable as-is -- it exists to prove the pipeline, not
  as a real edge.
- `backtest.py` -- runs a strategy against cached data via `backtrader`, prints return, max
  drawdown, Sharpe, win rate, and writes `webapp/results.json` for the dashboard.
- `paper_trade_alpaca.py` -- runs the same strategy live against an Alpaca **paper**
  account. `paper=True` is hardcoded, not a flag -- see its own docstring.
- `paper_trade.py` -- the original IBKR/futures version, kept in case futures come back
  into scope later. Needs IB Gateway running locally; see the git history of this file for
  its own setup steps if you go back to it.
- `webapp/` -- a local FastAPI dashboard (`webapp/main.py` + `webapp/static/index.html`)
  showing the latest backtest's equity curve, price chart with trade markers, and trade
  log. Run it with `venv\Scripts\python -m uvicorn webapp.main:app --port 8420` and open
  http://127.0.0.1:8420 -- re-running `backtest.py` and refreshing (or just waiting, it
  auto-refreshes every 5s) shows the new results.

## Setup

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt   # Windows; use venv/bin/pip on Mac/Linux
```

## Backtest loop (no broker account needed)

```bash
venv\Scripts\python fetch_data.py --ticker SPY --period 2y --interval 1d
venv\Scripts\python backtest.py --csv data/SPY.csv --fast 10 --slow 30 --shares 10
```

Try different `--fast`/`--slow`/`--shares` values, different tickers (`AAPL`, `QQQ`,
`TSLA`), `--allow-short`, different periods. The number to actually pay attention to isn't
the return -- it's whether the strategy beats plain buy-and-hold over the SAME window,
since a rising market makes almost any long-only strategy look good on paper.

## Paper trading (needs an Alpaca account -- free, no ID needed for paper)

1. Sign up free at [alpaca.markets](https://alpaca.markets). Paper trading works
   immediately after email signup -- no identity verification needed until you want to
   enable live trading with real money later.
2. Dashboard -> API Keys -> generate a **paper** key pair (separate from live keys --
   make sure it's the paper ones).
3. Set them as environment variables (Windows, then open a new terminal for it to take
   effect):
   ```
   setx ALPACA_API_KEY "your-key-id"
   setx ALPACA_SECRET_KEY "your-secret-key"
   ```
4. Run it:
   ```bash
   venv\Scripts\python paper_trade_alpaca.py --symbol SPY
   ```

`paper_trade_alpaca.py` checks the crossover once per run and exits -- it's meant to be
re-run on a schedule (cron/Task Scheduler), not left running as a daemon, at this stage.
Turning it into a long-running process that watches for crossovers continuously is a
reasonable next step once the once-per-run version has been checked against a few real
paper trades.

## What "proven" means before this goes anywhere near real money

- Backtest beats buy-and-hold over multiple different time windows, not just one lucky one.
- Walk-forward validated: parameters tuned on one period, tested (not re-tuned) on a later,
  unseen period -- otherwise "the best fast/slow combo" is just overfitting.
- A real stretch of paper-trading (weeks, not days) where live fills roughly match what the
  backtest expected -- slippage, spread, and the broker's actual fill behavior aren't in
  the backtest at all right now.

None of that exists yet. This is the plumbing, not the strategy.

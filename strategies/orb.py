"""
Opening Range Breakout (ORB) -- based on Zarattini & Aziz's 2023 "Can Day Trading Really
Be Profitable?" methodology: define the opening range as the first N minutes of the
session, enter on a breakout of that range, stop on the opposite side of the range,
flatten before the close (no overnight risk, no gap risk).

Needs INTRADAY bars (minute-level), not the daily bars every other strategy in this repo
uses -- backtest.py's CSV feed currently assumes daily data (dtformat "%Y-%m-%d", no time
component). Wiring this into backtest.py/tune_strategy.py needs a small feed change once
we actually have real intraday history to test against (the exact timestamp format depends
on whether it comes from IBKR or elsewhere) -- no point generalizing that plumbing before
there's real data to run it on.

Same proof-of-concept result as the pandas prototype this replaces: 60 days of free Yahoo
data showed this works on volatile individual names (TSLA, NVDA, MARA, RIOT, LCID) and not
on broad index ETFs (SPY, QQQ) -- but a wider 22-ticker sweep came back close to a coin
flip, so that's NOT a confirmed edge, just a shape worth testing properly on real history.
"""

import datetime

import backtrader as bt

from strategies.base import TrackedStrategy


class ORB(TrackedStrategy):
    params = dict(
        size=10,
        or_minutes=15,          # Opening range window length, in minutes.
        market_open=datetime.time(9, 30),
        market_close=datetime.time(16, 0),
        flatten_minutes_before_close=5,  # Force-close open positions this many minutes before the bell.
        allow_short=True,
    )

    def __init__(self):
        super().__init__()
        self.session_date = None
        self.or_high = None
        self.or_low = None
        self.or_window_open = None  # Precomputed: market_open + or_minutes, as a datetime.time.

        or_end_dt = (datetime.datetime.combine(datetime.date.today(), self.p.market_open)
                     + datetime.timedelta(minutes=self.p.or_minutes))
        self.or_window_end = or_end_dt.time()

        flatten_dt = (datetime.datetime.combine(datetime.date.today(), self.p.market_close)
                      - datetime.timedelta(minutes=self.p.flatten_minutes_before_close))
        self.flatten_time = flatten_dt.time()

    def next(self):
        self.record()

        current_date = self.data.datetime.date(0)
        current_time = self.data.datetime.time(0)

        if current_date != self.session_date:
            # New session -- reset the opening range, but any position left open from
            # yesterday should already be flat (flatten_time closes it same-day below).
            # If it somehow isn't (e.g. data starts mid-session), don't carry stale range
            # levels into today.
            self.session_date = current_date
            self.or_high = None
            self.or_low = None

        if current_time < self.p.market_open:
            return  # Pre-market bar in the feed -- ignore.

        if current_time < self.or_window_end:
            # Still inside the opening range -- accumulate its high/low, don't trade yet.
            high, low = self.data.high[0], self.data.low[0]
            self.or_high = high if self.or_high is None else max(self.or_high, high)
            self.or_low = low if self.or_low is None else min(self.or_low, low)
            return

        if self.or_high is None or self.or_low is None:
            return  # No opening range captured (e.g. feed gap) -- nothing to break out of.

        if self.order:
            return

        if current_time >= self.flatten_time:
            if self.position:
                self.order = self.close()
            return

        price = self.data.close[0]

        if not self.position:
            if price > self.or_high:
                self.order = self.buy(size=self.p.size)
            elif self.p.allow_short and price < self.or_low:
                self.order = self.sell(size=self.p.size)
        elif self.position.size > 0 and self.data.low[0] <= self.or_low:
            self.order = self.close()  # Long stopped out -- opposite side of the range.
        elif self.position.size < 0 and self.data.high[0] >= self.or_high:
            self.order = self.close()  # Short stopped out -- opposite side of the range.

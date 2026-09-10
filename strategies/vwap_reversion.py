"""
VWAP mean-reversion -- fade price extensions away from the session's volume-weighted
average price back toward it. VWAP and its volume-weighted standard deviation are computed
incrementally per session (closed-form: var = E[price^2] - E[price]^2, both weighted by
volume) rather than storing every bar, so this stays O(1) per bar regardless of session
length.

The research on this (see chat) is explicit that it "fails badly on strong trend days -- a
regime filter is mandatory." The trend_filter_* params are that filter: skip a new fade if
price has already moved more than trend_filter_threshold_pct over the last
trend_filter_period bars in the direction that would fight the fade (e.g. don't buy a dip
below VWAP if price is in the middle of a strong existing downtrend).
"""

import datetime

import backtrader as bt

from strategies.base import TrackedStrategy


class VWAPReversion(TrackedStrategy):
    params = dict(
        size=10,
        entry_std=2.0,          # Enter when price is this many std devs away from session VWAP.
        stop_std=3.0,            # Invalidation level -- further out than the entry band.
        trend_filter_period=30,  # Bars to look back for the crude trend-day filter.
        trend_filter_threshold_pct=0.5,
        market_open=datetime.time(9, 30),
        market_close=datetime.time(16, 0),
        flatten_minutes_before_close=5,
        allow_short=True,
    )

    def __init__(self):
        super().__init__()
        self.session_date = None
        self.cum_pv = 0.0
        self.cum_pv2 = 0.0
        self.cum_vol = 0.0

        flatten_dt = (datetime.datetime.combine(datetime.date.today(), self.p.market_close)
                      - datetime.timedelta(minutes=self.p.flatten_minutes_before_close))
        self.flatten_time = flatten_dt.time()

    def next(self):
        self.record()

        current_date = self.data.datetime.date(0)
        current_time = self.data.datetime.time(0)

        if current_date != self.session_date:
            self.session_date = current_date
            self.cum_pv = 0.0
            self.cum_pv2 = 0.0
            self.cum_vol = 0.0

        if current_time < self.p.market_open:
            return

        typical = (self.data.high[0] + self.data.low[0] + self.data.close[0]) / 3.0
        volume = self.data.volume[0] or 1.0  # A zero-volume bar shouldn't erase the running VWAP.
        self.cum_pv += typical * volume
        self.cum_pv2 += typical * typical * volume
        self.cum_vol += volume

        if self.cum_vol <= 0:
            return

        vwap = self.cum_pv / self.cum_vol
        variance = max(self.cum_pv2 / self.cum_vol - vwap * vwap, 0.0)
        std = variance ** 0.5
        if std == 0:
            return  # Not enough dispersion yet (e.g. session's first bar) to measure an extension.

        if self.order:
            return

        if current_time >= self.flatten_time:
            if self.position:
                self.order = self.close()
            return

        price = self.data.close[0]

        if len(self.data) > self.p.trend_filter_period:
            past_price = self.data.close[-self.p.trend_filter_period]
            trend_move_pct = 100 * (price - past_price) / past_price
        else:
            trend_move_pct = 0.0

        if not self.position:
            lower_band = vwap - self.p.entry_std * std
            upper_band = vwap + self.p.entry_std * std
            # Don't fade a move that's part of an already-strong trend in the same direction.
            if price < lower_band and trend_move_pct > -self.p.trend_filter_threshold_pct:
                self.order = self.buy(size=self.p.size)
            elif (self.p.allow_short and price > upper_band
                  and trend_move_pct < self.p.trend_filter_threshold_pct):
                self.order = self.sell(size=self.p.size)
        elif self.position.size > 0:
            stop_level = vwap - self.p.stop_std * std
            if price >= vwap or self.data.low[0] <= stop_level:
                self.order = self.close()
        elif self.position.size < 0:
            stop_level = vwap + self.p.stop_std * std
            if price <= vwap or self.data.high[0] >= stop_level:
                self.order = self.close()

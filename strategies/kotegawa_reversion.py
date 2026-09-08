"""
KotegawaReversion: tests the documented systematic piece of Takashi Kotegawa's ("BNF")
trading approach -- explicit user request, 2026-09-07, after researching his real track
record (see analyze_failures.py-style validation discipline applied here too). His publicly
documented method (NOT the one-off Mizuho fat-finger trade, which was a non-repeatable
exploit of a stranger's error, not a strategy) was short-term mean reversion: buy liquid
stocks after they've fallen 20-35% below their own 25-day moving average, confirmed with
RSI, Bollinger Bands, and volume ratio.

Meant to REPLACE the current live "rsi" agent in place if it validates better -- not to spin
up a third agent/ticker-universe/Railway service. If it doesn't beat the already-live
RSIReversion+stop_pct on the same honest walk-forward test, it stays unshipped, same as
every rejected experiment tonight.
"""

import backtrader as bt

from strategies.base import TrackedStrategy


class KotegawaReversion(TrackedStrategy):
    params = dict(
        ma_period=25,           # his own documented reference -- the 25-day moving average
        dip_pct=25,             # entry requires price this many % BELOW the 25-day MA (his range: 20-35%)
        rsi_period=14,
        rsi_oversold=30,        # RSI confirmation -- must ALSO be oversold, not just "far below MA"
        bb_period=20,
        bb_dev=2.0,             # standard Bollinger Band deviation
        min_volume_ratio=1.2,   # volume confirmation -- the drop needs above-average volume behind it
        volume_ma_period=20,
        recovery_pct=10,        # exit once price recovers to within this % of the 25-day MA (thesis played out)
        max_hold_days=10,
        stop_pct=0.10,          # a deep-dip entry needs more room than a shallow one -- wider than RSIReversion's 5%
        size=10,
    )

    def __init__(self):
        super().__init__()
        self.ma = bt.indicators.SMA(self.data.close, period=self.p.ma_period)
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.bb = bt.indicators.BollingerBands(self.data.close, period=self.p.bb_period, devfactor=self.p.bb_dev)
        self.volume_ma = bt.indicators.SMA(self.data.volume, period=self.p.volume_ma_period)
        self.entry_bar = None
        self.entry_price = None

    def next(self):
        self.record()
        if self.order:
            return

        if not self.position:
            dip_pct = 100 * (self.ma[0] - self.data.close[0]) / self.ma[0]
            oversold = self.rsi[0] < self.p.rsi_oversold
            below_band = self.data.close[0] < self.bb.lines.bot[0]
            volume_ratio = self.data.volume[0] / self.volume_ma[0] if self.volume_ma[0] else 0
            confirmed_volume = volume_ratio >= self.p.min_volume_ratio

            if not (dip_pct >= self.p.dip_pct and oversold and below_band and confirmed_volume):
                return

            self.order = self.buy(size=self.p.size)
            self.entry_bar = len(self)
            self.entry_price = self.data.close[0]
            return

        bars_held = len(self) - self.entry_bar
        recovered = 100 * (self.ma[0] - self.data.close[0]) / self.ma[0] <= self.p.recovery_pct
        stopped_out = self.data.close[0] <= self.entry_price * (1 - self.p.stop_pct)
        if bars_held >= self.p.max_hold_days or recovered or stopped_out:
            self.order = self.close()

"""
ReliefShort: a genuinely different short hypothesis from the two already tested and
rejected (Breakdown's trend-following "short the new low", RSIReversion's untrended "fade
any overbought spike"). This fades a RELIEF RALLY specifically -- short only while the stock
is ALREADY in a confirmed downtrend (same trend filter as Breakdown) AND RSI has spiked into
overbought territory (a bounce, not a fresh breakdown), betting the bounce fails and the
downtrend resumes. Exit when RSI falls back out of overbought (the bounce has failed, thesis
played out) or hits a stop-loss/max_hold_days.

Why this is different, not just a rescaled version of something already tried:
RSIReversion's own allow_short mode fades overbought with NO trend filter at all -- it'll
short a relief bounce in a downtrend the same as it shorts genuine strength in a raging
uptrend, and the latter is a well-known way to lose money fighting a real trend. Breakdown
chases NEW LOWS (trend-following) rather than fading exhaustion (mean-reversion) -- a
structurally different bet on what actually predicts the next move.

Paper-only, same as everything else here -- this needs the SAME walk-forward validation
(TRAIN then held-out TEST) as Breakout got before it's trusted with anything, validated or
not on its own merits before ever being combined with Breakout.

RESULT (2026-09-06): inconclusive, not disproven -- "below the 50/100-day trend AND
RSI(14) simultaneously above 55-70" is simply too rare a confluence to generate a usable
trade count. Zero trades across all 18 grid combos on the TRAIN/TEST windows (the original 8
tickers barely ever have a real downtrend at all), and only 0-1 trades PER TICKER even on the
2022 bear-market year at a lenient RSI>55 threshold. By the time a bounce is sharp enough to
push a 14-day RSI that high, price has often already crossed back above a 50-day trend line.
Would need either a faster RSI, a shorter/more responsive trend filter, or testing across
much more bear-market history to get a fair sample -- not attempted yet.
"""

import backtrader as bt

from strategies.base import TrackedStrategy


class ReliefShort(TrackedStrategy):
    params = dict(
        trend_period=50,     # price must be BELOW this SMA -- only fade rallies within a confirmed downtrend
        rsi_period=14,
        overbought=70,       # RSI above this while in a downtrend = a relief rally worth fading
        exit_rsi=40,         # RSI falling back below this = the bounce failed, cover
        stop_pct=0.05,       # hard stop-loss if the "rally" just keeps going instead of failing
        max_hold_days=10,
        size=10,
    )

    def __init__(self):
        super().__init__()
        self.trend_ma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.entry_bar = None
        self.entry_price = None

    def next(self):
        self.record()
        if self.order:
            return

        if not self.position:
            in_downtrend = self.data.close[0] < self.trend_ma[0]
            relief_rally = self.rsi[0] > self.p.overbought
            if not (in_downtrend and relief_rally):
                return

            self.order = self.sell(size=self.p.size)  # sell to open -- goes short
            self.entry_bar = len(self)
            self.entry_price = self.data.close[0]
            return

        bars_held = len(self) - self.entry_bar
        bounce_failed = self.rsi[0] < self.p.exit_rsi
        stopped_out = self.data.close[0] >= self.entry_price * (1 + self.p.stop_pct)
        if bars_held >= self.p.max_hold_days or bounce_failed or stopped_out:
            self.order = self.close()  # buy to close -- covers the short

"""
Trend-filtered breakout swing strategy: only look for entries while price is above its own
long-term trend average (the "only trade with the trend" filter classic trend-followers like
Turtle Trading and Darvas Box systems both use), then buy when price breaks above its recent
`breakout_period`-day high -- a fresh high within an established uptrend, not a breakout in a
downtrend or sideways chop. Exits on a hard stop-loss, a fixed profit target, or
`max_hold_days`, whichever comes first.

Long-only -- breakout systems are directional by nature (a "breakdown" version betting on
new lows within a downtrend is a legitimate mirror strategy, but is a separate thing to
build and test on its own, not a same-file allow_short flag like the other two strategies).

FAILURE ANALYSIS (2026-09-05, explicit user request -- "learn from failure"): every entry now
logs three things about the setup itself (how far above the breakout level, how far above
volume trend, how far above the price trend) alongside the eventual win/loss, via
closed_trades. min_volume_ratio/min_breakout_margin_pct are OFF by default (0) -- set them
only after analyze_failures.py has shown a real, out-of-sample-confirmed split between
winners and losers, not as a guess.
"""

import backtrader as bt

from strategies.base import TrackedStrategy


class Breakout(TrackedStrategy):
    params = dict(
        trend_period=50,      # price must be above this SMA to allow any entry at all
        breakout_period=20,   # entry: today's close breaks above the highest close of the PRIOR N days
        stop_pct=0.05,        # hard stop-loss, as a fraction below entry price
        target_pct=0.08,      # profit target, as a fraction above entry price
        max_hold_days=10,
        size=10,
        volume_ma_period=20,
        # Extra entry filters, both OFF (0) by default -- see analyze_failures.py. Only turn
        # these on with values that analysis actually supported on a TRAIN window, then
        # confirm on a held-out TEST window before trusting the result.
        min_volume_ratio=0,        # require today's volume >= this x the volume_ma_period average
        min_breakout_margin_pct=0,  # require the close to clear the breakout level by at least this %
        # 2026-09-05 analyze_failures.py run: winners averaged 6.15% above trend at entry,
        # losers 7.42% -- a lean (fresher breakouts hold up better), not a cliff. OFF (0) by
        # default until validate_trend_cap.py confirms a specific value on the held-out TEST
        # window; see that file for the walk-forward check.
        max_trend_strength_pct=0,  # reject entries more than this % above the trend SMA (0 = no cap)
        # 2026-09-05 analyze_failures.py: breakout_margin_pct runs BACKWARDS from intuition --
        # losers cleared the breakout level by MORE (avg 1.34%) than winners did (avg 1.05%),
        # so this is a ceiling, not the existing min_breakout_margin_pct floor. Also OFF (0)
        # by default until validate_entry_filters.py confirms a value on the TEST window.
        max_breakout_margin_pct=0,  # reject entries clearing the breakout level by more than this % (0 = no cap)
    )

    def __init__(self):
        super().__init__()
        self.trend_ma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)
        # data.close(-1) shifts the whole series back one bar before taking the rolling max,
        # so "highest" is the highest close over the PRIOR breakout_period days -- excluding
        # today's own close, which would otherwise make every single bar trivially "a
        # breakout" against itself.
        self.highest = bt.indicators.Highest(self.data.close(-1), period=self.p.breakout_period)
        self.volume_ma = bt.indicators.SMA(self.data.volume, period=self.p.volume_ma_period)
        self.entry_bar = None
        self.entry_price = None
        self._pending_features = None
        self.closed_trades = []  # one dict per CLOSED round-trip: entry features + outcome

    def next(self):
        self.record()
        if self.order:
            return

        if not self.position:
            in_uptrend = self.data.close[0] > self.trend_ma[0]
            broke_out = self.data.close[0] > self.highest[0]
            if not (in_uptrend and broke_out):
                return

            volume_ratio = self.data.volume[0] / self.volume_ma[0] if self.volume_ma[0] else 0
            breakout_margin_pct = 100 * (self.data.close[0] - self.highest[0]) / self.highest[0]
            trend_strength_pct = 100 * (self.data.close[0] - self.trend_ma[0]) / self.trend_ma[0]
            if volume_ratio < self.p.min_volume_ratio or breakout_margin_pct < self.p.min_breakout_margin_pct:
                return  # setup qualifies on trend+breakout but fails the extra quality filter
            if self.p.max_trend_strength_pct and trend_strength_pct > self.p.max_trend_strength_pct:
                return  # too far above trend already -- less room left before a pullback
            if self.p.max_breakout_margin_pct and breakout_margin_pct > self.p.max_breakout_margin_pct:
                return  # cleared the breakout level by too much -- historically the weaker setup

            self.order = self.buy(size=self.p.size)
            self.entry_bar = len(self)
            self.entry_price = self.data.close[0]
            self._pending_features = {
                "breakout_margin_pct": round(breakout_margin_pct, 2),
                "volume_ratio": round(volume_ratio, 2),
                "trend_strength_pct": round(trend_strength_pct, 2),
            }
            return

        bars_held = len(self) - self.entry_bar
        stopped_out = self.data.close[0] <= self.entry_price * (1 - self.p.stop_pct)
        hit_target = self.data.close[0] >= self.entry_price * (1 + self.p.target_pct)
        if bars_held >= self.p.max_hold_days or stopped_out or hit_target:
            self.order = self.close()

    def notify_trade(self, trade):
        # backtrader's own per-trade callback, separate from notify_order -- fires once when
        # a round-trip (entry through exit) actually closes, with the real realized pnl
        # already computed. This is what closed_trades needs; notify_order alone only ever
        # sees individual fills, not a paired entry/exit outcome.
        if trade.isclosed and self._pending_features is not None:
            self.closed_trades.append({
                "pnl": trade.pnl,
                "won": trade.pnl > 0,
                **self._pending_features,
            })
            self._pending_features = None

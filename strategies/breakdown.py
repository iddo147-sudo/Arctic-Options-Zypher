"""
Breakdown: the short mirror of strategies/breakout.py, explicitly called out there as "a
legitimate mirror strategy... a separate thing to build and test on its own" rather than a
same-file allow_short flag (2026-09-05 user request -- "i need him to master shorts as
well"). Only look for entries while price is BELOW its own long-term trend average (in a
confirmed downtrend), then SHORT when price breaks below its recent `breakdown_period`-day
low -- a fresh low within an established downtrend, not a breakdown in an uptrend or
sideways chop. Exits on a hard stop-loss (price rises against the short), a fixed profit
target (price falls further), or `max_hold_days`, whichever comes first.

Shorting carries real extra risk a long-only strategy doesn't (theoretically unlimited loss
if the stock keeps rising, harder/costlier borrow, more prone to short squeezes) -- this is
paper-only, same as everything else in this project, and stays that way until proven the
same way Breakout was: full walk-forward validation on a held-out TEST window, not trusted
on the "shorts are just the mirror of longs" assumption alone.

No entry-quality filters yet (min_volume_ratio, trend-strength caps, etc.) -- Breakout
itself started with none and only added them after analyze_failures.py found real,
TEST-confirmed leads. Ship the core mirror first, validate it, THEN consider filters --
same staged-rollout discipline, not skipping straight to a fully-loaded version.
"""

import backtrader as bt

from strategies.base import TrackedStrategy


class Breakdown(TrackedStrategy):
    params = dict(
        trend_period=50,        # price must be BELOW this SMA to allow any entry at all
        breakdown_period=20,    # entry: today's close breaks below the lowest close of the PRIOR N days
        stop_pct=0.05,          # hard stop-loss, as a fraction ABOVE entry price (price rising against the short)
        target_pct=0.08,        # profit target, as a fraction BELOW entry price
        max_hold_days=10,
        size=10,
    )

    def __init__(self):
        super().__init__()
        self.trend_ma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)
        # Mirror of Breakout's self.highest -- lowest close over the PRIOR breakdown_period
        # days, excluding today's own close (same close(-1) shift, same reasoning: today's
        # own close would otherwise trivially be "a breakdown" against itself every bar).
        self.lowest = bt.indicators.Lowest(self.data.close(-1), period=self.p.breakdown_period)
        self.entry_bar = None
        self.entry_price = None
        self._pending_features = None
        self.closed_trades = []  # one dict per CLOSED round-trip: entry features + outcome

    def next(self):
        self.record()
        if self.order:
            return

        if not self.position:
            in_downtrend = self.data.close[0] < self.trend_ma[0]
            broke_down = self.data.close[0] < self.lowest[0]
            if not (in_downtrend and broke_down):
                return

            breakdown_margin_pct = 100 * (self.lowest[0] - self.data.close[0]) / self.lowest[0]
            trend_weakness_pct = 100 * (self.trend_ma[0] - self.data.close[0]) / self.trend_ma[0]

            self.order = self.sell(size=self.p.size)  # sell to open -- goes short
            self.entry_bar = len(self)
            self.entry_price = self.data.close[0]
            self._pending_features = {
                "breakdown_margin_pct": round(breakdown_margin_pct, 2),
                "trend_weakness_pct": round(trend_weakness_pct, 2),
            }
            return

        bars_held = len(self) - self.entry_bar
        # Mirrored directions from Breakout: a short is stopped out if price rises against
        # it, hits its target if price falls further.
        stopped_out = self.data.close[0] >= self.entry_price * (1 + self.p.stop_pct)
        hit_target = self.data.close[0] <= self.entry_price * (1 - self.p.target_pct)
        if bars_held >= self.p.max_hold_days or stopped_out or hit_target:
            self.order = self.close()  # buy to close -- covers the short

    def notify_trade(self, trade):
        if trade.isclosed and self._pending_features is not None:
            self.closed_trades.append({
                "pnl": trade.pnl,
                "won": trade.pnl > 0,
                **self._pending_features,
            })
            self._pending_features = None

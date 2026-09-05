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
        # 2026-09-05 evening, "train him harder": stop_pct/target_pct are the SAME fixed %
        # for every stock regardless of how volatile it actually is -- 5% is tight for TSLA
        # and loose for a utility. ATR (average true range) scales the exit to each stock's
        # OWN recent volatility instead. OFF (atr_period=0) by default -- only replaces the
        # fixed stop_pct/target_pct above when set; see validate_atr_exits.py for the
        # walk-forward check before trusting this over the already-validated fixed version.
        atr_period=0,          # 0 = off, use stop_pct/target_pct above unchanged
        stop_atr_mult=2.0,     # stop = entry_price - this many ATRs (at entry)
        target_atr_mult=3.0,   # target = entry_price + this many ATRs (at entry)
        # Trailing stop, independent of the fixed-vs-ATR choice above: once in a position,
        # ratchets the stop up toward (highest close since entry - this many ATRs), never
        # back down -- lets a winner run past the original target instead of capping it,
        # classic trend-following exit. 0 = off (original behavior: stop/target fixed at
        # entry, never move). Needs atr_period set (uses the same ATR reading).
        trailing_stop_atr_mult=0,
        # 2026-09-05 evening, round 2 of "train him harder": a genuinely different
        # hypothesis than the exit-shape experiments above -- does requiring RSI
        # confirmation (momentum actually supportive, not overbought/exhausted or weak)
        # improve which breakouts get taken? OFF (both 0) by default; see
        # validate_rsi_confirmation.py for the walk-forward check.
        rsi_period=14,
        min_rsi=0,  # reject entries with RSI below this (0 = no floor) -- avoids breakouts with weak underlying momentum
        max_rsi=0,  # reject entries with RSI above this (0 = no ceiling) -- avoids buying an already-overbought/exhausted move
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
        # Only created when actually needed -- an indicator with period=0 would error, and
        # atr_period=0 (off) is the default. bt.indicators.ATR is backtrader's own Wilder
        # true-range average, needs High/Low (present in every cached CSV -- see fetch_data.py).
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period) if self.p.atr_period else None
        # Always created (cheap, and rsi_period is never 0) -- only actually gates entries
        # when min_rsi/max_rsi are set, same "compute it, only filter on it if asked" shape
        # as volume_ratio/breakout_margin_pct/trend_strength_pct above.
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.entry_bar = None
        self.entry_price = None
        self.atr_at_entry = None
        self.highest_close_since_entry = None
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
            if self.p.min_rsi and self.rsi[0] < self.p.min_rsi:
                return  # momentum too weak to trust this breakout
            if self.p.max_rsi and self.rsi[0] > self.p.max_rsi:
                return  # already overbought/exhausted -- less room left before a pullback

            self.order = self.buy(size=self.p.size)
            self.entry_bar = len(self)
            self.entry_price = self.data.close[0]
            self.atr_at_entry = self.atr[0] if self.atr is not None else None
            self.highest_close_since_entry = self.data.close[0]
            self._pending_features = {
                "breakout_margin_pct": round(breakout_margin_pct, 2),
                "volume_ratio": round(volume_ratio, 2),
                "trend_strength_pct": round(trend_strength_pct, 2),
            }
            return

        self.highest_close_since_entry = max(self.highest_close_since_entry, self.data.close[0])

        # ATR-scaled stop/target when atr_period is set, otherwise the original fixed %.
        if self.atr_at_entry:
            stop_price = self.entry_price - self.p.stop_atr_mult * self.atr_at_entry
            # target_atr_mult=0 means "no fixed target at all" -- rely entirely on the
            # trailing stop (or max_hold_days) to exit, the classic trend-following shape
            # of "let winners run" instead of capping every winner at a fixed multiple.
            target_price = (self.entry_price + self.p.target_atr_mult * self.atr_at_entry
                             if self.p.target_atr_mult else float("inf"))
        else:
            stop_price = self.entry_price * (1 - self.p.stop_pct)
            target_price = self.entry_price * (1 + self.p.target_pct)

        # Trailing stop only ever tightens (moves the effective stop up), never loosens it
        # below whatever the fixed/ATR stop already was -- so this can only help a winner
        # keep more profit, never make a loser's stop worse.
        if self.p.trailing_stop_atr_mult and self.atr is not None:
            trailing_price = self.highest_close_since_entry - self.p.trailing_stop_atr_mult * self.atr[0]
            stop_price = max(stop_price, trailing_price)

        bars_held = len(self) - self.entry_bar
        stopped_out = self.data.close[0] <= stop_price
        hit_target = self.data.close[0] >= target_price
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

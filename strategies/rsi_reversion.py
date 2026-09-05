"""
RSI mean-reversion swing strategy: buy when RSI drops into oversold territory (a sharp
short-term decline, betting on a bounce), hold until RSI recovers past `exit_rsi` or
`max_hold_days` passes, whichever comes first. One of the most well-documented short-horizon
swing archetypes -- Larry Connors' RSI-2 research (Connors Research, "Short Term Trading
Strategies That Work") is the classic reference, though that uses a much shorter (2-period)
RSI than the classic 14-period used here as the default; both are worth backtesting, hence
`rsi_period` being a parameter rather than hardcoded.

Long-only by default (buying oversold bounces); allow_short bets the opposite (fading
overbought spikes), which is a real but historically weaker edge in trending markets --
included for comparison, not because it's expected to win.
"""

import backtrader as bt

from strategies.base import TrackedStrategy


class RSIReversion(TrackedStrategy):
    params = dict(
        rsi_period=14,
        oversold=30,    # RSI below this on a flat position triggers a long entry
        exit_rsi=55,    # RSI recovering above this on a long position exits (the bounce played out)
        max_hold_days=10,
        size=10,
        allow_short=False,
    )

    def __init__(self):
        super().__init__()
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.entry_bar = None

    def next(self):
        self.record()
        if self.order:
            return

        if not self.position:
            if self.rsi[0] < self.p.oversold:
                self.order = self.buy(size=self.p.size)
                self.entry_bar = len(self)
            elif self.p.allow_short and self.rsi[0] > (100 - self.p.oversold):
                self.order = self.sell(size=self.p.size)
                self.entry_bar = len(self)
            return

        bars_held = len(self) - self.entry_bar
        long_recovered = self.position.size > 0 and self.rsi[0] > self.p.exit_rsi
        short_recovered = self.position.size < 0 and self.rsi[0] < (100 - self.p.exit_rsi)
        if bars_held >= self.p.max_hold_days or long_recovered or short_recovered:
            self.order = self.close()

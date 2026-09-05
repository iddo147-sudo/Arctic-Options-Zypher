"""
Starting strategy: fast SMA crosses above slow SMA -> long; crosses back below -> either
flat (allow_short=False) or flip short (allow_short=True). Futures don't need borrowed
shares to short like stocks do, so long/short is symmetric here -- no special-casing.

This is a STARTING POINT, not a strategy anyone should expect to be profitable out of the
box -- plain MA crossover is one of the most well-known, most arbitraged-away patterns in
retail trading. Its job here is to prove the pipeline (data -> signal -> order -> fill)
works end to end; the real strategy work is what replaces this once that's confirmed.
"""

import backtrader as bt

from strategies.base import TrackedStrategy


class MACrossover(TrackedStrategy):
    params = dict(
        fast_period=10,
        slow_period=30,
        # Contracts per trade. Left at 1 on purpose -- MES is the micro contract
        # specifically so "1" is already a small, real position, not a placeholder.
        size=1,
        # Off by default so the original long-only behavior (and anyone's existing
        # backtest comparisons against it) doesn't silently change underneath them.
        allow_short=False,
    )

    def __init__(self):
        super().__init__()
        self.fast_ma = bt.indicators.SMA(self.data.close, period=self.p.fast_period)
        self.slow_ma = bt.indicators.SMA(self.data.close, period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)

    def next(self):
        self.record()

        if self.order:
            return  # one order in flight at a time -- no pyramiding into a pending fill
        if self.crossover == 0:
            return  # no new cross this bar -- hold whatever position we already have

        # Target position size, then a single order for whatever delta gets us there --
        # handles every starting state (flat, long, short) the same way, including opening
        # a short straight from flat if the very first cross happens to be bearish.
        target = self.p.size if self.crossover > 0 else (-self.p.size if self.p.allow_short else 0)
        delta = target - self.position.size
        if delta > 0:
            self.order = self.buy(size=delta)
        elif delta < 0:
            self.order = self.sell(size=-delta)

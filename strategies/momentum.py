"""
Momentum / relative-strength swing strategy: buy when the stock has moved up sharply over
the last `lookback` days (it's "in play"), hold for up to `max_hold_days`, exit early if
that momentum clearly stalls or reverses. A well-documented archetype for the 1-2 week swing
window specifically -- momentum has real, published academic backing (Jegadeesh & Titman's
"Returns to Buying Winners and Selling Losers", 1993, is the classic reference) at monthly
horizons; this is a much shorter-horizon variant, which is a different (and less proven)
regime -- exactly why it needs backtesting here rather than being trusted on reputation.

Long-only by default. Shorting a stock in freefall is symmetric in theory but carries real
extra risk (unlimited loss potential, harder borrow) that a first pass doesn't need to take
on -- allow_short exists for comparison, same convention as MACrossover.
"""

from strategies.base import TrackedStrategy


class Momentum(TrackedStrategy):
    params = dict(
        lookback=10,          # trading days the momentum measurement looks back over
        entry_threshold=0.04,  # +4% over `lookback` days triggers a long entry
        exit_threshold=-0.01,  # momentum dropping to -1% (from wherever it was) exits early
        max_hold_days=10,      # ~2 trading weeks -- exit regardless of signal past this
        size=10,
        allow_short=False,
    )

    def __init__(self):
        super().__init__()
        self.entry_bar = None

    def _momentum(self):
        if len(self) <= self.p.lookback:
            return 0.0
        past = self.data.close[-self.p.lookback]
        if past == 0:
            return 0.0
        return (self.data.close[0] - past) / past

    def next(self):
        self.record()
        if self.order:
            return

        momentum = self._momentum()

        if not self.position:
            if momentum > self.p.entry_threshold:
                self.order = self.buy(size=self.p.size)
                self.entry_bar = len(self)
            elif self.p.allow_short and momentum < -self.p.entry_threshold:
                self.order = self.sell(size=self.p.size)
                self.entry_bar = len(self)
            return

        bars_held = len(self) - self.entry_bar
        long_stalled = self.position.size > 0 and momentum < self.p.exit_threshold
        short_stalled = self.position.size < 0 and momentum > -self.p.exit_threshold
        if bars_held >= self.p.max_hold_days or long_stalled or short_stalled:
            self.order = self.close()

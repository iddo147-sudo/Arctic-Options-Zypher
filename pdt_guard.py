"""
Pattern Day Trader (PDT) rule guard -- FINRA/broker rule: under $25k account equity, no more
than 3 day trades (opening AND closing the same position same session) in any ROLLING 5
TRADING-DAY window, or the account gets flagged and day-trading gets restricted. It's a
rolling window, not a calendar week -- 2 day trades Thu+Fri then 2 more Mon+Tue is 4 inside
one 5-trading-day window and trips it, even though it's "2 a week" on a calendar. This class
exists so an agent can check "am I actually allowed to day trade today" before firing an
entry, rather than trusting a fixed weekly counter that would be wrong near week boundaries.

Trading-day counting here approximates "trading day" as a weekday (Mon-Fri) and does not
account for market holidays -- fine for staying safely under the limit (holidays only make
the real window shorter than 5 calendar weekdays, never longer), not for maximizing trade
count right up against the edge.
"""

import datetime


PDT_EQUITY_THRESHOLD = 25_000
MAX_DAY_TRADES_PER_WINDOW = 3
WINDOW_TRADING_DAYS = 5


def _is_weekday(d: datetime.date) -> bool:
    return d.weekday() < 5


def _trading_days_back(as_of: datetime.date, n: int) -> datetime.date:
    """The date that is n trading days before as_of (approximating trading days as weekdays)."""
    d = as_of
    counted = 0
    while counted < n:
        d -= datetime.timedelta(days=1)
        if _is_weekday(d):
            counted += 1
    return d


class PDTGuard:
    def __init__(self):
        self.day_trade_dates: list[datetime.date] = []

    def record_day_trade(self, date: datetime.date):
        self.day_trade_dates.append(date)

    def count_in_window(self, as_of: datetime.date) -> int:
        window_start = _trading_days_back(as_of, WINDOW_TRADING_DAYS - 1)
        return sum(1 for d in self.day_trade_dates if window_start <= d <= as_of)

    def can_day_trade(self, as_of: datetime.date, account_equity: float) -> bool:
        if account_equity >= PDT_EQUITY_THRESHOLD:
            return True  # Rule doesn't apply above the equity threshold.
        return self.count_in_window(as_of) < MAX_DAY_TRADES_PER_WINDOW

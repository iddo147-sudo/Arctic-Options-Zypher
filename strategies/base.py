"""
Shared bookkeeping every strategy in this project needs -- an equity curve, price series,
and trade log for the dashboard, plus the standard one-order-in-flight guard. Pulled out
once other strategies started needing the exact same tracking code MACrossover already had,
rather than copy-pasting it into every new file.

Subclasses implement next() for their own entry/exit logic. Call self.record() at the top
of next() (before any early return) so every bar gets logged even on bars where nothing
happens -- an equity curve with gaps on "do nothing" days would misrepresent flat stretches
as missing data.
"""

import backtrader as bt


class TrackedStrategy(bt.Strategy):
    def __init__(self):
        self.order = None
        self.equity_curve = []
        self.price_series = []
        self.trade_log = []

    def log(self, text):
        dt = self.data.datetime.date(0)
        print(f"{dt.isoformat()} {text}")

    def record(self):
        self.equity_curve.append({
            "date": self.data.datetime.date(0).isoformat(),
            "value": self.broker.getvalue(),
        })
        self.price_series.append({
            "date": self.data.datetime.date(0).isoformat(),
            "close": self.data.close[0],
        })

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return
        if order.status == order.Completed:
            side = "BUY" if order.isbuy() else "SELL"
            self.log(f"{side} filled @ {order.executed.price:.2f}")
            self.trade_log.append({
                "date": self.data.datetime.date(0).isoformat(),
                "side": side,
                "price": order.executed.price,
                "size": order.executed.size,
            })
        elif order.status in (order.Canceled, order.Margin, order.Rejected):
            self.log(f"Order failed: {order.getstatusname()}")
        self.order = None

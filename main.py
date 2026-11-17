"""Post-washout base rider for btc-race-24h-20261117-0603.

Trades cbBTC 1-minute bars (bars-replay/v1) with $1,000 of paper money,
long only, full size. The idea: wait for price to wash out to a rolling
low, base there for a while, then bounce back up near the rolling high
while the short trend is still flat-to-up -- and ride the rally that
follows the washout, with a hard stop, a profit target, a trailing
lock-in once the trade is in profit, and a time stop.

Everything is integer math on the raw close prices (scale cancels in
ratios), so two runs on the same tape produce identical orders. No
clock, no network, no files, no randomness.

Strategy parameters (measured on the frozen 0603 dev tape):
    lookback          360 bars for the rolling high / low (closes)
    entry: price at least 120 bps below the 360-bar high (the washout
           dip), at least 40 bps above the 360-bar low, that low at
           least 10 bars old, 60-bar return no worse than -80 bps,
           price above SMA(60), and the last 30 bars' low at least
           20 bps above the rolling low (a higher low)
    exits:  target +175 bps, stop -110 bps, lock-in arms at +80 bps
            and then trails 60 bps under the peak since entry,
            time stop at 300 bars held
    sizing: full cash on every buy, full position on every sell
    cooldown: no new entry until more than 120 bars after an exit
              fill (the exit bar plus 120 bars must pass first)
    max_slippage_bps: 10000 on every order
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

# --- strategy parameters (basis points unless noted) -------------------------
LOOKBACK = 360            # bars for the rolling high / low
MAX_DD_BPS = 120          # entry: price at least this far below the rolling high
BOUNCE_BPS = 40           # entry: price at least this above the rolling low
LOW_MIN_AGE = 10          # entry: the rolling low must be at least this old (bars)
RET60_MIN_BPS = -80       # entry: 60-bar return no worse than this
SMA_LEN = 60              # entry: price must sit above SMA(this)
HL_WINDOW = 30            # entry: higher-low window (bars)
HL_MIN_BPS = 20           # entry: last-HL_WINDOW low at least this above rolling low
HOLD_MAX = 300            # exit: time stop, bars held
TARGET_BPS = 175          # exit: take profit
STOP_BPS = 110            # exit: hard stop
ARM_BPS = 80              # exit: lock-in arms once price is this far above entry
TRAIL_BPS = 60            # exit: after arming, sell this far under the peak
COOLDOWN = 120            # bars to wait after an exit before re-entering
MAX_SLIP_BPS = 10000      # max_slippage_bps on every order

BPS = 10_000


def bps_move(price, ref):
    """(price - ref) / ref in basis points, integer-truncated toward zero."""
    return (price - ref) * BPS // ref


class Rider:
    """Keeps the bar history and the open-trade bookkeeping."""

    def __init__(self):
        self.closes = []          # close price per bar, raw ints
        self.entry_price = None   # fill price of the open trade (raw price units)
        self.peak = None          # highest close since entry
        self.fill_bar = -1        # bar index of the entry fill
        self.cool_until = -1      # bar index: no entry while bar_no <= this
        self.bar_no = -1          # index of the latest bar seen

    # -- fills ---------------------------------------------------------------
    def note_fills(self, fills):
        """Fold this batch's fills into the trade bookkeeping."""
        for fill in fills or []:
            if fill.get("symbol") != SYMBOL or fill.get("status") != "filled":
                continue
            price = int(fill["price"])
            if fill["side"] == "buy":
                self.entry_price = price
                self.peak = price
                self.fill_bar = self.bar_no
            elif fill["side"] == "sell":
                self.entry_price = None
                self.peak = None
                self.fill_bar = -1
                self.cool_until = self.bar_no + COOLDOWN

    # -- entry ---------------------------------------------------------------
    def entry_ok(self, price):
        """All entry gates, evaluated on the closes seen so far."""
        closes = self.closes
        if len(closes) < LOOKBACK:
            return False
        if self.bar_no <= self.cool_until:
            return False
        window = closes[-LOOKBACK:]
        high = max(window)
        low = min(window)
        # The rolling low must be an old base, not today's dip.
        low_age = len(self.closes) - 1 - (len(self.closes) - LOOKBACK + window.index(low))
        if low_age < LOW_MIN_AGE:
            return False
        # Washed out from the high: price at least MAX_DD_BPS below it.
        if bps_move(price, high) > -MAX_DD_BPS:
            return False
        # Bounced off the low.
        if bps_move(price, low) < BOUNCE_BPS:
            return False
        # Short trend not falling away: 60-bar return and SMA(60).
        if bps_move(price, closes[-61]) < RET60_MIN_BPS:
            return False
        if price <= sum(closes[-SMA_LEN:]) // SMA_LEN:
            return False
        # Higher low: the recent range bottom sits above the rolling low.
        if bps_move(min(closes[-HL_WINDOW:]), low) < HL_MIN_BPS:
            return False
        return True

    # -- exit ----------------------------------------------------------------
    def exit_now(self, price):
        """Which exit, if any, fires on this bar's close. Returns a reason or None."""
        if self.entry_price is None:
            return None
        entry = self.entry_price
        if price > self.peak:
            self.peak = price
        gain = bps_move(price, entry)
        if gain >= TARGET_BPS:
            return "target"
        if gain <= -STOP_BPS:
            return "stop"
        if self.peak is not None and bps_move(self.peak, entry) >= ARM_BPS:
            if bps_move(price, self.peak) <= -TRAIL_BPS:
                return "trail"
        if self.bar_no - self.fill_bar >= HOLD_MAX:
            return "time"
        return None


RIDER = Rider()


def on_batch(state, batch):
    bar = None
    for b in batch.get("bars", []):
        if b.get("symbol") == SYMBOL:
            bar = b
            break
    if bar is None:
        return []
    RIDER.bar_no += 1
    RIDER.note_fills(batch.get("fills"))
    price = int(bar["c"])
    RIDER.closes.append(price)

    if state.warming_up:
        return []

    holding = state.positions.get(SYMBOL)
    if holding is not None and holding.qty > 0:
        if RIDER.exit_now(price) is not None:
            return [{
                "side": "sell",
                "symbol": SYMBOL,
                "qty": holding.qty,
                "max_slippage_bps": MAX_SLIP_BPS,
            }]
        return []

    if RIDER.entry_ok(price) and state.cash > 0:
        return [{
            "side": "buy",
            "symbol": SYMBOL,
            "notional_quote_units": state.cash,
            "max_slippage_bps": MAX_SLIP_BPS,
        }]
    return []


makefun_bars.run(on_batch)

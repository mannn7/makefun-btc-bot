"""A trend bot for make.fun btc-race-24h-20261118-1444: race a BTC trading bot live for 24 hours.

Strategy (simple, robust, no fitted magic):
  * EMA(60) vs EMA(120) of the 1-minute cbBTC closes, seeded with the mean of the
    first span of closes, exactly like the kit's ema-cross baseline.
  * Enter long (all cash) only when the fast EMA crosses above the slow one by more
    than 5 bps AND the trailing 60-bar return is above 40 bps: the cross alone
    fires in chop, the momentum confirmation keeps the bot out of it.
  * Exit the whole position when the fast EMA falls back under the slow one, or at
    +1.5% over cost (take profit), or at -1.0% under cost (stop loss). All three
    exits are standing conditions, so a rejected sell is retried on the next bar.
  * Fallback: if the bot has still not traded by bar 1380 it buys on the first bar
    with a positive 60-bar return, and unconditionally by bar 1420, so the entry
    always meets the race guardrail of at least one trade (bounded by the -1% stop
    and the final flatten).
  * One position at most, long only, full cash per entry; max slippage 100 bps.

Determinism: no randomness, no clock, no network, no files outside the repo.
Every decision comes from the bars the harness sent, in order. The bot speaks
agent-fun/bars-replay/v1 through the kit's Python shim (vendor/makefun_bars.py,
a byte-identical copy of the kit's sdk/makefun_bars.py).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

FAST_SPAN = 60
SLOW_SPAN = 120
GAP_ENTER_BPS = 5.0          # fast EMA must cross above slow by more than this
RET_LOOKBACK = 60            # bars for the momentum confirmation
RET_ENTER_BPS = 40.0         # trailing 60-bar return must beat this to enter
TP_NUM, TP_DEN = 1015, 1000  # take profit: position worth 1.5% over cost
STOP_NUM, STOP_DEN = 99, 100  # stop loss: position worth 1.0% under cost
MAX_SLIPPAGE_BPS = 100       # 1%: never binds on the dev window (max 1m move 64 bps)
MIN_NOTIONAL = 1_000_000     # 1 USDC in quote units

FALLBACK_WEAK_BAR = 1380     # from here, buy on any bar with a positive 60-bar return
FALLBACK_LAST_BAR = 1420     # from here, buy unconditionally (still stop-guarded)


class Ema:
    """One EMA over closes, seeded with the mean of its first span of closes."""

    __slots__ = ("span", "k", "warm", "value")

    def __init__(self, span):
        self.span = span
        self.k = 2.0 / (span + 1)
        self.warm = []
        self.value = None

    def push(self, close):
        if self.value is not None:
            self.value += self.k * (close - self.value)
            return
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span


class Bot:
    def __init__(self):
        self.closes = []
        self.last_t = None
        self.fast = Ema(FAST_SPAN)
        self.slow = Ema(SLOW_SPAN)
        self.gap = None       # (fast/slow - 1) in bps after the latest bar, or None
        self.prev_gap = None  # the same after the bar before
        self.pend_cross = False    # a confirmed cross is waiting to buy
        self.pend_fallback = False  # the fallback is waiting to buy
        self.traded = False        # any of our orders has filled

    def ret60(self):
        """Trailing 60-bar return in bps, or None without 61 closes."""
        if len(self.closes) <= RET_LOOKBACK:
            return None
        return (self.closes[-1] / self.closes[-1 - RET_LOOKBACK] - 1.0) * 10000.0

    def on_batch(self, state, batch):
        # 1. Fold in a fresh CBBTC bar (a gap minute carries none) and step the EMAs.
        for bar in batch["bars"]:
            if bar["symbol"] != SYMBOL:
                continue
            t = int(bar["t"])
            if self.last_t is not None and t <= self.last_t:
                continue
            self.last_t = t
            close = float(int(bar["c"]))
            self.closes.append(close)
            self.prev_gap = self.gap
            self.fast.push(close)
            self.slow.push(close)
            if self.fast.value is not None and self.slow.value is not None:
                self.gap = (self.fast.value / self.slow.value - 1.0) * 10000.0
            else:
                self.gap = None
        # 2. Fills: a filled buy clears any pending buy and marks us traded.
        for fill in batch.get("fills", []):
            if fill.get("status") == "filled":
                self.traded = True
                if fill.get("side") == "buy":
                    self.pend_cross = False
                    self.pend_fallback = False
        if state.warming_up:
            return []
        held = state.positions.get(SYMBOL)
        # 3. A pending cross dies when the trend turns down or the momentum fades.
        if self.gap is not None and self.gap < 0:
            self.pend_cross = False
        ret = self.ret60()
        if ret is not None and ret <= RET_ENTER_BPS:
            self.pend_cross = False
        # 4. Exits (standing conditions, so a rejected sell is retried next bar).
        if held is not None and held.qty > 0:
            value = state.value(SYMBOL, held.qty)
            cost = held.cost
            cross_down = self.gap is not None and self.gap < 0
            take_profit = cost > 0 and value * TP_DEN >= cost * TP_NUM
            stop_loss = cost > 0 and value * STOP_DEN <= cost * STOP_NUM
            if cross_down or take_profit or stop_loss:
                return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []
        # 5. Entries: a fresh cross above 5 bps with momentum above 40 bps.
        if (self.gap is not None and self.prev_gap is not None
                and self.prev_gap <= GAP_ENTER_BPS and self.gap > GAP_ENTER_BPS
                and ret is not None and ret > RET_ENTER_BPS):
            self.pend_cross = True
        # 6. Fallback so the entry always makes at least one trade.
        if not self.traded:
            if state.seq >= FALLBACK_LAST_BAR:
                self.pend_fallback = True
            elif state.seq >= FALLBACK_WEAK_BAR and ret is not None and ret > 0:
                self.pend_fallback = True
        # 7. Send at most one buy: fallback first, then the confirmed cross.
        if self.pend_fallback and state.cash >= MIN_NOTIONAL:
            return [{"side": "buy", "symbol": SYMBOL,
                     "notional_quote_units": state.cash,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        if (self.pend_cross and self.gap is not None and self.gap > GAP_ENTER_BPS
                and ret is not None and ret > RET_ENTER_BPS
                and state.cash >= MIN_NOTIONAL):
            return [{"side": "buy", "symbol": SYMBOL,
                     "notional_quote_units": state.cash,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []


makefun_bars.run(Bot().on_batch)

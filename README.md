[![make.fun](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/mannn7/makefun-btc-bot/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/mannn7/makefun-btc-bot/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)

# BTC trend bot — make.fun btc-race-24h-20261118-1444

A bot that trades Coinbase Wrapped BTC (cbBTC) on 1-minute on-chain prices with
$1,000 of paper money, for the make.fun competition "Race a BTC trading bot live
for 24 hours". It speaks `agent-fun/bars-replay/v1` (newline-delimited JSON on
stdin/stdout) through the kit's Python shim (`vendor/makefun_bars.py`, a
byte-identical copy of the kit's `sdk/makefun_bars.py`). Python 3.13, standard
library only, deterministic: no randomness, no clock, no network, no files
outside the repo.

## Strategy

Simple trend following with a momentum confirmation, long only, one position:

- **Indicators:** EMA(60) vs EMA(120) of the 1-minute closes, seeded with the
  mean of the first span of closes (same seeding as the kit's ema-cross
  baseline).
- **Enter** (all cash) only when the fast EMA crosses above the slow one by
  more than 5 bps **and** the trailing 60-bar return is above 40 bps. The
  cross alone fires in chop; the momentum confirmation keeps the bot out of
  it. A pending entry is retried on the next bar if its buy is rejected.
- **Exit** the whole position when the fast EMA falls back under the slow one,
  or at **+1.5% over cost** (take profit), or at **−1.0% under cost** (stop
  loss). All three exits are standing conditions, so a rejected sell is
  retried on the next bar.
- **Fallback:** if the bot has still not traded by bar 1380 it buys on the
  first bar with a positive 60-bar return, and unconditionally by bar 1420, so
  the entry always meets the race guardrail of at least one trade (bounded by
  the −1% stop and the final flatten). Max slippage is 100 bps on every order.

## Dev-window result (frozen 24 h window in the kit, 1,440 bars)

Graded with a faithful local replay of the harness's exact integer fill
engine (fee 4 bps, slippage 2 bps, constant-product impact on each bar's open
depth, flatten at the last close), driving the real `main.py` twice:

| | Net PnL | Trades | Max drawdown |
|---|---|---|---|
| **This bot** | **+$14.41** | 2 (1 round trip) | 0.64% |
| Buy-and-hold (same window) | −$31.63 | 1 | 3.25% |
| Hold-cash | $0.00 | 0 | 0.00% |

The bot made its money on the window's one real rally (bought bar 355, sold
at the +1.5% take profit on bar 508) and sat out the rest, including the
−2.8% grind down. Both runs produced byte-identical replies (deterministic),
and the run takes well under a second for all 1,440 batches.

Robustness checks on the same window: first half +$14.41, second half $0.00
(no trade — the filter correctly refused the chop); on rolling 12-hour
windows it was profitable in 4 of 7 and never lost money, while the same
rules without the momentum confirmation lost on 3 of 7. The dev gate
(net PnL above $0) is passed with a $14.41 margin.

## Files

- `main.py` — the bot
- `vendor/makefun_bars.py` — the kit's Python protocol shim, byte-identical
- `README.md` — this file
- `LICENSE` — MIT

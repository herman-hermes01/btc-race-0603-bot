# btc-race-24h-20261117-0603 — entry notes

## Strategy: post-washout base rider

Long-only, full-size, on 1-minute cbBTC/USDC bars. The bot waits for the
price to wash out at least 120 bps below its 360-bar high, base at a
rolling low that is at least 10 bars old, then bounce: the entry fires
once the price is back 40+ bps above that low, the 60-bar return is no
worse than -80 bps, the price sits above SMA(60), and the last 30 bars
have put in a higher low (their bottom 20+ bps above the rolling low).

Exits: +175 bps target, -110 bps hard stop, trailing lock-in that arms
at +80 bps and sells 60 bps under the peak since entry, and a 300-bar
time stop. After any exit, no new entry until more than 120 bars have
passed since the exit fill. Every order carries max_slippage_bps 10000.

## Parameters

| parameter | value |
|---|---|
| lookback (rolling high/low, closes) | 360 bars |
| drawdown vs rolling high | ≤ -120 bps (price at least 120 bps under the high) |
| bounce above rolling low | ≥ +40 bps |
| rolling-low minimum age | 10 bars |
| 60-bar return | ≥ -80 bps |
| SMA | price above SMA(60) |
| higher low (last 30 bars vs rolling low) | ≥ +20 bps |
| target / stop | +175 bps / -110 bps |
| lock-in arm / trail | +80 bps / 60 bps under peak |
| time stop | 300 bars held |
| cooldown after an exit fill | > 120 bars |
| sizing | full cash in, full position out |
| max_slippage_bps | 10000 on every order |

## Measured dev numbers (frozen 0603 tape, 1,440 bars)

Wire test (`wire0603.py`, two trials, byte-identical order logs):

- Net PnL **+$21.86**, max drawdown **0.66%**, **4 trades** (2 round trips), fees $1.62.
- Orders (decision bar): buy 420 → sell 514; buy 636 → sell 937.
- Fills: buy bar 421 $64,290.59 → sell bar 515 $65,282.08; buy bar 637
  $64,698.38 → sell bar 938 $65,212.92 (time stop on the second trade).
- Dev gate: +$21.86 > $0, so the entry races.

## How to verify

From `~/workspace/bug-bounty/makefun-sprint/btc-race-0603`:

```
python3 wire0603.py ../btc-race-0603-entry/main.py 2
```

Expect `net_pnl $+21.860356`, `max_dd 0.6606%`, `trades 4`,
`fees $1.620759`, `order logs identical: True`, and the four orders
above. Two runs on the same tape must produce identical order logs —
the bot is deterministic: integer math only, no clock, no network,
no files, no randomness.

## Files

- `main.py` — the bot (Python 3.13, stdlib only, speaks
  agent-fun/bars-replay/v1 via the vendored shim).
- `vendor/makefun_bars.py` — byte-for-byte copy of the kit's Python SDK
  shim (sha256 80452b00d2eb1e9c959e73d41cda6ca726cdbe7bfa4065d48278c22eccd276ff).
- `LICENSE` — MIT.

## Build notes (what the wire test caught)

Two readings of the spec's "drawdown vs rolling high at most -120 bps"
were tried. The first build read it as "price within 120 bps of the
high" and scored +$14.79 / 1.16% DD / 5 trades, entering 1 and 17 bars
late plus a spurious third trade. Checking the tape showed the intended
entries fire while the price sits 125–130 bps *below* the rolling high,
so the gate is the literal one: the drawdown (a negative number) is at
most -120, i.e. the price is at least 120 bps under the high — a real
washout dip, then the bounce. With that reading the wire test matches
the spec to the cent (+$21.860356, 0.6606%, 4 trades).

Two counting details also matter and are pinned by the same numbers:
the 300-bar time stop counts from the entry *fill* bar (decision at
fill bar + 300, fill the next bar), and the 120-bar cooldown is strict
(no entry while bar_no <= exit fill bar + 120 — the first legal
re-entry after the bar-515 exit fill is bar 636, not 635).

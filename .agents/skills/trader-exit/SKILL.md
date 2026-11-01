---
name: trader-exit
description: >-
  Binance 24/7 Exit Planner. Sets mathematical stop-loss and take-profit targets
  before placing a crypto trade. Reads trigger entry from trader/entry.json
  (or flags), computes stop-loss (opening-range midpoint, range low, fixed price,
  percentage, or ATR from live Binance klines), and sets take-profit targets
  at 1R, 2R, or higher. Replaces stock market close with configurable crypto
  time-stops (default 4 hours) or trailing stops. Writes trader/plan.json for
  /trader-timemachine and /trader-budget. Use when the user asks "where is my stop",
  "set stop and target", "plan crypto exit", "risk reward ratio", or after /trader-entry.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X] [--time-stop 4h]"
---

# trader-exit

The Binance Exit Planner determines how a trade ends before capital is committed.
The stop-loss defines where the trade thesis has failed. The take-profit target
defines where gains are banked. In 24/7 crypto trading, there is no 4:00 PM
closing bell: trades use configurable time-stops (e.g. 4 hours) or trailing
stops so positions do not remain exposed indefinitely.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                                      # stop at midpoint, 2R target, 4h time-stop
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --from trader/entry.json --write     # writes trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop pct:2 --target 2R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5 --time-stop 4h        # live ATR from Binance klines
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol SOLUSDT --entry 102.32 --stop 100.27 --target 106.42
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                                      # view current trader/plan.json
```

Flags: `--stop or-mid|or-low|pct:X|atr:K|price:X`, `--target 1R|2R|3R|price:X`,
`--time-stop 4h` (configurable maximum hold time), `--trailing-stop 1.5%`, `--write`, `--json`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | entry trigger from trader-entry: symbol, entry price, time, setup |
| `trader/config.json` | `flat_by` or default time-stop (`+4h`), `min_reward_risk` (1.0) |
| Binance API | live 5m klines for dynamic ATR stop calculation |
| `trader/plan.json` | written plan: symbol, entry, stop, target, risk per unit, reward:risk, time-stop |

## Stop Rules & 24/7 Time-Stops

| rule | calculation |
| --- | --- |
| `or-mid` | opening-range midpoint of the active UTC session |
| `or-low` | session opening-range low |
| `pct:X` | X percent below entry price (e.g. `pct:2` for 2% stop) |
| `atr:K` | K times the 14-period Average True Range on 5m Binance candles |
| `time-stop` | closes the trade if neither stop nor target is hit within N hours (default 4h) |

## Flow: what to run next

1. Run `/trader-exit --from trader/entry.json --write` to establish the risk parameters.
2. Run `/trader-timemachine SOLUSDT` to simulate 10,000 historical Binance replays.
3. Run `/trader-budget` to size the fractional crypto position to your risk budget.

## Rules

- The stop-loss must be strictly below the entry price for a long trade.
- The take-profit target must be strictly above the entry price for a long trade.
- Minimum reward:risk ratio must meet or exceed your config rule (default 1.0).

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user confirmed execution.
- Keys live in environment variables (`BINANCE_API_KEY`, `BINANCE_SECRET_KEY`), never in the chat.

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap or a fast market can fill past it. Day trading loses most people money,
and a neat plan does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader/entry.json: midpoint stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars (Yahoo or --offline)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol TSLA --entry 364.18 --or-high 362.18 --or-low 354.47
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

Other flags: `--flat-by 15:55`, `--json`, `--now "2026-10-19 10:15"` (New
York time), `--entry PRICE` with a waiting entry.json to plan ahead, `--setup
level`, `--offline FILE` (a saved Yahoo chart JSON for an ATR stop).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:55) and `min_reward_risk` (1.0: a note here, trader-risk enforces it) |
| Yahoo chart API | only for an `atr:K` stop: the session's bars up to the entry bar, free and unofficial |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, flat by. trader-timemachine, trader-budget and trader-alerts read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar. With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **Rounding.** The stop rounds AWAY from the entry to the cent (down, for a
  long). Risk = entry minus stop. Target = entry + R x risk, rounded half up
  to the cent. `2R` by default; `1R`, `1.5R` or `price:X` also work.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price: too tight to survive the
  spread, normal wiggle would stop you out. An entry.json that has not
  triggered (unless you give `--entry`). A short: v2 plans longs only.
- **Flat by.** 15:55 by default (or `flat_by` in the config). Out by then,
  win or lose, no overnight hold. On a 1:00 p.m. early close it moves up to
  12:55. An entry at or after the flat-by time is refused.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk and flat by.
4. If it refuses, say the reason and the fix (a wider stop, a farther
   target). Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/trader-timemachine` to replay the plan, then `/trader-budget`
   to size it to the max loss per trade. Offer `/trader-alerts` for the stop,
   the target and the flat-by time. Claude can keep `entry.py watch` running
   in the background or on a `/loop` until a setup triggers, then come here.

## Example: the reel

TSLA, Monday 2026-10-19. The entry is 364.18 at 10:15 AM (the 10:10 bar,
bar 8) and the opening range is 354.47 to 362.18. `exit.py plan --stop
or-mid --target 1R --write`:

```
Exit plan: TSLA long, Mon 2026-10-19, opening-range breakout on 5-minute bars
  Entry         $364.18 at 10:15 AM (bar 8)
  Stop          $358.32  opening-range midpoint: ($362.18 + $354.47) / 2 = $358.325, rounded down
  Target        $370.04  1R (one times the risk)
  Risk a share  $5.86 (1.61% of the entry)
  Reward:risk   1.0
  Flat by       15:55 ET, win or lose: no overnight hold
```

The default 2R target would be 364.18 + 2 x 5.86 = 375.90. trader-budget then
sizes it: a $50 max loss per trade buys floor(50 / 5.86) = 8 shares.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. It
  never prints a P&L or a return.
- Nothing here promises winning trades or money. A 1R target is not a
  prediction that the price gets there.
- Long side only. v2 plans longs only.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.

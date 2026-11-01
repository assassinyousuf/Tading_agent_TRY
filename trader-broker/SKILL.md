---
name: trader-broker
description: >-
  Binance 24/7 Order Executor. Executes approved crypto orders on Binance Spot
  and USD(S)-M Futures, defaulting to Testnet ("paper": true) and never without
  your explicit yes. Displays plain-English tickets with stop-loss, take-profit,
  fractional quantities, and exact Binance taker fees (0.10% Spot / 0.05% Futures).
  Persists intent to .state/orders_journal.json. Signs requests using HMAC-SHA256
  standard library via binance_broker.py. Supports Spot Post-Fill OCO, Atomic OTOCO,
  Futures Algo conditional exits, ambiguous submission recovery, and bounded
  emergency recovery. Use when the user asks "execute order", "buy on Binance",
  "send to broker", "connect Binance testnet", or after /trader-risk passes.
argument-hint: "[order number | status | execute | --dry-run]"
---

# trader-broker

The Binance 24/7 Order Executor sends verified bracket orders to Binance Spot or
USD(S)-M Futures. It enforces a strict triple safety gate before any request
reaches the market:

1. **trader-risk PASS**: The exact order must have passed all risk checks today
   with a matching SHA-256 fingerprint in `trader/state/risk.json`.
2. **Explicit User Yes**: The user must explicitly approve the order in chat
   (`ticket.py approve --index 1 --reply "yes"`).
3. **Double Safety Gate for Live**: Default mode is Testnet (`"paper": true`).
   Live execution with real capital strictly requires `"paper": false` in config
   AND the explicit `--confirm-live` CLI flag.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
# Direct CLI execution (default mode is --dry-run)
python3 "${CLAUDE_SKILL_DIR}/scripts/binance_broker.py" --dry-run --symbol SOLUSDT --side BUY --qty 1.25 --entry 150.10 --stop 147.50 --target 155.30

# Subcommands
python3 "${CLAUDE_SKILL_DIR}/binance_broker.py" status                     # testnet connectivity and USDT balances
python3 "${CLAUDE_SKILL_DIR}/binance_broker.py" execute --dry-run          # verify gate checks without sending
python3 "${CLAUDE_SKILL_DIR}/binance_broker.py" execute                    # execute on Testnet (or Live if confirmed)
python3 "${CLAUDE_SKILL_DIR}/binance_broker.py" positions                  # inspect open positions and balances
python3 "${CLAUDE_SKILL_DIR}/binance_broker.py" cancel --symbol SOLUSDT    # cancel open orders
python3 "${CLAUDE_SKILL_DIR}/binance_broker.py" flatten --symbol SOLUSDT   # trigger bounded emergency recovery
```

## Free Binance Testnet Setup

1. Obtain a free Testnet API key and secret:
   - Spot Testnet: https://testnet.binance.vision/
   - Futures Testnet: https://testnet.binancefuture.com/
2. Export your credentials in your shell:
   ```bash
   export BINANCE_API_KEY="your_testnet_api_key"
   export BINANCE_SECRET_KEY="your_testnet_secret_key"
   ```
3. Test connectivity with `python trader-broker/binance_broker.py status`.

## Order Execution Workflows

1. **Spot Market Entry + Post-Fill OCO**:
   - Submits `POST /api/v3/order` with `newClientOrderId`.
   - Reconciles fill status via `GET /api/v3/order` and calculates net sellable filled quantity (deducting base fee).
   - Submits protective exit via `POST /api/v3/orderList/oco` (`aboveType=LIMIT_MAKER`, `belowType=STOP_LOSS_LIMIT` or `STOP_LOSS`).
2. **Spot Pending Limit Entry (Atomic OTOCO)**:
   - Submits `POST /api/v3/orderList/otoco` with working limit order paired with pending OCO bracket.
3. **USD(S)-M Futures Entry + Algo Conditional Exits**:
   - Queries `GET /fapi/v1/positionSide/dual` for One-Way Mode (`positionSide="BOTH"`, `reduceOnly=True`) vs Hedge Mode.
   - Submits entry via `POST /fapi/v1/order` and conditional SL/TP exits via Futures Algo Service `POST /fapi/v1/algoOrder`.

## Ambiguous Submission Recovery & Bounded Emergency Recovery

- **Ambiguous Submission**: If an order times out or returns HTTP 5xx, it is recorded in `.state/orders_journal.json` as `AMBIGUOUS`. The broker always queries status endpoints before retrying.
- **Bounded Emergency Recovery**: If a protective SL/TP leg is rejected or fails:
  1. Circuit-breaker lock engaged halting all new entries.
  2. Queries live exposure (`GET /api/v3/account` Spot, `GET /fapi/v2/positionRisk` Futures).
  3. Cancels orphaned exit orders to prevent double-execution.
  4. Submits a strictly bounded closing order sized to verified exposure.
  5. Re-queries account to verify exposure is reduced to zero without looping.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | proposed orders from trader-budget |
| `trader/state/risk.json` | trader-risk verdicts; only PASS orders can execute |
| `trader/state/broker.json` | records explicit user yes and execution order IDs |
| `.state/orders_journal.json` | pre-submission order intent, client IDs, and circuit-breaker state |
| `trader/portfolio.json` | updated crypto balances and positions |

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user confirmed execution.
- Keys live in environment variables (`BINANCE_API_KEY`, `BINANCE_SECRET_KEY`), never in the chat.

# GSD ROADMAP: Binance Crypto Trading Toolkit Migration

## Project Overview
Migrating `day-trader-skill` to a 24/7 Binance Spot & USDⓈ-M Futures trading desk using **pure Python standard library** (zero pip dependencies) with strict `decimal.Decimal` financial precision, dynamic pagination, and robust error reconciliation.

---

## Phase 1: Core Binance Client & Precision Engine (`binance_client.py`)
- [ ] 1.1: Standard library HTTP client with timeout, weight tracking (`x-mbx-used-weight-1m`), backoff, and 429/418 `Retry-After` handling.
- [ ] 1.2: Exact HMAC-SHA256 signature generation with pre-signing URL percent-encoding.
- [ ] 1.3: `decimal.Decimal` exchange filter quantization (`PRICE_FILTER`, `LOT_SIZE`, `MARKET_LOT_SIZE`, `NOTIONAL`, `MIN_NOTIONAL`).
- [ ] 1.4: Separate Spot (`api.binance.com` / `testnet.binance.vision`) and Futures (`fapi.binance.com` / `testnet.binancefuture.com`) endpoints.
- [ ] 1.5: Unit tests for client signing and filter precision.

## Phase 2: Market Scanner (`trader-scanner/scan.py`)
- [ ] 2.1: Binance 24/7 USDT universe discovery (filtering `TRADING`, excluding stablecoins & leveraged tokens).
- [ ] 2.2: Rolling UTC session ORB (00:00 Asian, 08:00 London, 13:30 NY) on 5m candles.
- [ ] 2.3: 4-Hour Volatility Squeeze scanner (Bollinger Bands vs. Keltner Channels).
- [ ] 2.4: Backward compatibility with stock offline replay fixtures so existing tests pass.

## Phase 3: Historical Engine & Timemachine (`trader-timemachine/timemachine.py`)
- [ ] 3.1: Dynamic backward pagination using `endTime` for `api/v3/klines` (1,000 candle limit per call).
- [ ] 3.2: Candle continuity, timestamp ordering, gap detection, and exclusion of unclosed candles.
- [ ] 3.3: Backtester simulating taker fees (0.1% spot, 0.05% futures) + slippage with R-expectancy, max drawdown, and out-of-sample metrics.
- [ ] 3.4: Preserve CLI interface: `--symbol SOLUSDT --interval 15m --days 60`.

## Phase 4: Sizing, Budget & Risk Engine (`trader-budget/size.py`, `trader-risk/check.py`)
- [ ] 4.1: Eliminate FINRA $25k PDT guard.
- [ ] 4.2: USDT balance support and `Decimal` position sizing rounded down to `stepSize`.
- [ ] 4.3: Notional filter checks, fee inclusion, and daily loss lockout logic.

## Phase 5: Trade Entry & Exit Engine (`trader-entry/entry.py`, `trader-exit/exit.py`)
- [ ] 5.1: Real-time 1m/5m kline polling from Binance for crypto pairs.
- [ ] 5.2: Replace 3:55 PM ET close rule with configurable time-stop (e.g., 4h) and ATR trailing stop.
- [ ] 5.3: Price and quantity quantization to exact `tickSize` and `stepSize`.

## Phase 6: Broker Execution Layer (`trader-broker/binance_broker.py`, `trader-broker/ticket.py`)
- [ ] 6.1: Deterministic `newClientOrderId` generation and persistent order state.
- [ ] 6.2: Spot execution: Market/Limit entry + modern OCO (`POST /api/v3/orderList/oco`).
- [ ] 6.3: Futures execution: Market/Limit entry + Algo conditional orders (`POST /fapi/v1/algoOrder`) with dual position-mode detection.
- [ ] 6.4: Order reconciliation on ambiguous timeout/5xx (query by `origClientOrderId` before retry).
- [ ] 6.5: Dry-run and paper-trading defaults with strict live confirmation.

## Phase 7: Verification & Test Suite
- [ ] 7.1: Comprehensive offline test suite in `tests/test_binance.py`.
- [ ] 7.2: Verify all tests in `tests/` pass with zero live network calls.

## Phase 8: Documentation & Agent Skills
- [ ] 8.1: Update `SKILL.md` files across all `trader-*` folders.
- [ ] 8.2: Populate `.agents/skills/` for direct agent invocation.

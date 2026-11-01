---
name: trader-scanner
description: >-
  Binance 24/7 Crypto & USD(S)-M Futures Scanner. Discovers and scans top volume
  USDT trading pairs on Binance public REST API. Filters eligible symbols dynamically
  via exchangeInfo (excluding stablecoins and leveraged tokens), filters by 24h quote
  volume >= 10M USDT, and fetches 5m/15m klines using ThreadPoolExecutor bounded concurrency.
  Detects rolling UTC session opening-range breakouts (00:00 Asian open, 08:00 London open,
  13:30 NY open) and 4-hour Bollinger Band / ATR volatility squeeze breakouts with RVOL
  confirmation. Writes trader/watchlist.json for trader-entry. Use when the user asks to
  "scan crypto", "scan Binance", "opening range breakout", "squeeze breakouts", or before /trader-entry.
argument-hint: "[universe | squeeze | orb | daily] [--limit N] [--workers N] [--market spot|futures]"
---

# trader-scanner

The Binance Crypto Scanner is the first step of a trade. It discovers liquid
USDT pairs and scans candles for setups: rolling UTC session opening-range
breakouts (ORB) and 4-hour volatility squeeze breakouts. A scan is a list of
charts matching mathematical rules, not a recommendation to buy.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
# Universe generation (top liquid USDT pairs, excluding stablecoins and leveraged tokens)
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --limit 30 --out trader/universe.txt

# 4-hour volatility squeeze scanner with ThreadPoolExecutor bounded concurrency
python3 "${CLAUDE_SKILL_DIR}/scan.py" squeeze --limit 30 --workers 4

# Rolling UTC session opening-range breakout scanner
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --limit 30 --workers 4 --market spot

# Scan specific pairs
python3 "${CLAUDE_SKILL_DIR}/scan.py" squeeze --symbols BTCUSDT,SOLUSDT,ETHUSDT
```

## Market Eligibility & Filters

1. **Exchange Trading Rules**:
   - Queries `exchangeInfo` first to filter symbols with `status == "TRADING"` and `quoteAsset == "USDT"`.
   - Spot: `isSpotTradingAllowed == True`. Futures: `contractType == "PERPETUAL"`.
   - Strictly excludes stablecoin base assets (`USDC`, `FDUSD`, `TUSD`, `USDP`, `DAI`, `BUSD`, `EUR`, `AEUR`)
     and leveraged tokens (`UP`, `DOWN`, `BULL`, `BEAR`).
2. **Liquidity Floor**:
   - Filters 24h ticker for `quoteVolume >= 10,000,000` USDT and ranks by volume.
3. **Bounded Concurrency**:
   - Uses `ThreadPoolExecutor` (default 4 workers) to fetch klines without triggering rate limits.

## 24/7 Rolling UTC Sessions & 4-Hour Squeeze

1. **Rolling UTC Sessions**:
   Crypto trades 24/7. The scanner divides the day into three global liquidity sessions:
   - `00:00 UTC`: Asian Session Open (Tokyo, Singapore, Hong Kong)
   - `08:00 UTC`: London / European Session Open
   - `13:30 UTC`: New York / US Session Open
   The opening range is calculated from the first 15 minutes of the current session.

2. **4-Hour Volatility Squeeze Scanner**:
   Measures Bollinger Bands (20 period, 2.0 std dev) inside Keltner Channels
   (20 period, 1.5 ATR). When Bollinger Bands contract inside Keltner Channels,
   volatility is compressed. When the bands expand outside with RVOL > 1.2, a directional
   breakout fires.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | top liquid USDT pairs from Binance ticker API |
| `trader/watchlist.json` | candidate pairs and breakout metrics for trader-entry |

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Orders require explicit user yes confirmation before execution in trader-broker.
- Strict Isolation: trader-scanner never imports or invokes broker signing or order execution code.

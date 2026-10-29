#!/usr/bin/env python3
"""Binance REST API Client for Spot and USD(S)-M Futures.

Pure Python 3.8+ standard library. Zero external pip dependencies.
Implements:
- Spot and USD(S)-M Futures REST endpoints (Live & Testnet)
- Strict Decimal arithmetic for LOT_SIZE, PRICE_FILTER, and NOTIONAL
- HMAC-SHA256 signature generation with pre-signing percent-encoding
- Dynamic backward klines pagination with gap detection
- Request weight tracking and 429/418 Retry-After handling
- Idempotent order reconciliation and Algo order support
"""

import datetime
import decimal
from decimal import Decimal, ROUND_DOWN
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("binance_client")

# API Host URLs
SPOT_LIVE_HOST = "https://api.binance.com"
SPOT_TESTNET_HOST = "https://testnet.binance.vision"
FUTURES_LIVE_HOST = "https://fapi.binance.com"
FUTURES_TESTNET_HOST = "https://testnet.binancefuture.com"

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/assassinyousuf/day-trader-skill)"

INTERVAL_MS = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "8h": 8 * 60 * 60 * 1000,
    "12h": 12 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "3d": 3 * 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000,
}


class BinanceAPIError(Exception):
    def __init__(self, message, code=None, status_code=None, response_body=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.response_body = response_body


class BinanceClient:
    def __init__(self, api_key=None, api_secret=None, product="spot", testnet=False, timeout=15):
        self.product = product.lower()
        self.testnet = bool(testnet)
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("BINANCE_SECRET_KEY", "")
        self.used_weight_1m = 0
        self.time_offset_ms = 0
        self.base_url = self._resolve_base_url()

    def _resolve_base_url(self):
        if self.product == "futures":
            return FUTURES_TESTNET_HOST if self.testnet else FUTURES_LIVE_HOST
        return SPOT_TESTNET_HOST if self.testnet else SPOT_LIVE_HOST

    def set_time_offset(self, server_time_ms):
        """Calibrate local clock to Binance server time."""
        local_ms = int(time.time() * 1000)
        self.time_offset_ms = server_time_ms - local_ms

    def get_timestamp(self):
        """Current timestamp in ms adjusted for server time offset."""
        return int(time.time() * 1000) + self.time_offset_ms

    def sign_params(self, params):
        """Sign request query parameters using HMAC-SHA256."""
        if not self.api_secret:
            raise BinanceAPIError("API secret required for authenticated requests")
        p = dict(params)
        if "timestamp" not in p:
            p["timestamp"] = self.get_timestamp()
        if "recvWindow" not in p:
            p["recvWindow"] = 5000
        # Ensure parameters are percent-encoded consistently before signing
        query_string = urllib.parse.urlencode(p)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        p["signature"] = signature
        return p

    def request(self, method, endpoint, params=None, signed=False, max_retries=3):
        params = dict(params or {})
        headers = {"User-Agent": UA, "Accept": "application/json"}
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        if signed:
            params = self.sign_params(params)

        url = self.base_url + endpoint
        encoded_query = urllib.parse.urlencode(params) if params else ""
        data = None

        if method.upper() in ("GET", "DELETE"):
            if encoded_query:
                url += "?" + encoded_query
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = encoded_query.encode("utf-8")

        for attempt in range(1, max_retries + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    # Update rate limit weight tracking
                    weight_header = resp.headers.get("x-mbx-used-weight-1m")
                    if weight_header:
                        try:
                            self.used_weight_1m = int(weight_header)
                        except ValueError:
                            pass
                    raw_text = resp.read().decode("utf-8", "replace")
                    try:
                        return json.loads(raw_text)
                    except json.JSONDecodeError:
                        return {"raw": raw_text}
            except urllib.error.HTTPError as e:
                status_code = e.code
                error_body = e.read().decode("utf-8", "replace")
                parsed_code = None
                parsed_msg = error_body
                try:
                    err_json = json.loads(error_body)
                    parsed_code = err_json.get("code")
                    parsed_msg = err_json.get("msg", error_body)
                except Exception:
                    pass

                # Handle 429 Too Many Requests and 418 IP Ban
                if status_code in (429, 418):
                    retry_after = e.headers.get("Retry-After")
                    sleep_time = int(retry_after) if retry_after and retry_after.isdigit() else (attempt * 2.0)
                    logger.warning("Binance rate limit %d on %s, backing off for %.1fs", status_code, endpoint, sleep_time)
                    time.sleep(sleep_time)
                    if attempt == max_retries:
                        raise BinanceAPIError("Rate limit exceeded: %s" % parsed_msg, code=parsed_code, status_code=status_code)
                    continue

                # Transient 5xx server errors
                if status_code >= 500 and attempt < max_retries:
                    time.sleep(attempt * 1.0)
                    continue

                raise BinanceAPIError(
                    "Binance API error %d: %s" % (status_code, parsed_msg),
                    code=parsed_code,
                    status_code=status_code,
                    response_body=error_body
                )
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < max_retries:
                    time.sleep(attempt * 1.0)
                    continue
                raise BinanceAPIError("Network connection error to %s: %s" % (url, e))

    # --- Market Data ---

    def get_time(self):
        """Fetch server time and calibrate time offset."""
        endpoint = "/fapi/v1/time" if self.product == "futures" else "/api/v3/time"
        res = self.request("GET", endpoint)
        server_time = res.get("serverTime")
        if server_time:
            self.set_time_offset(int(server_time))
        return server_time

    def get_exchange_info(self, symbol=None):
        """Fetch exchange symbol metadata and trading rules."""
        endpoint = "/fapi/v1/exchangeInfo" if self.product == "futures" else "/api/v3/exchangeInfo"
        params = {"symbol": symbol.upper()} if symbol else None
        return self.request("GET", endpoint, params=params)

    def get_ticker_24hr(self, symbol=None):
        """Fetch 24hr ticker volume and price statistics."""
        endpoint = "/fapi/v1/ticker/24hr" if self.product == "futures" else "/api/v3/ticker/24hr"
        params = {"symbol": symbol.upper()} if symbol else None
        return self.request("GET", endpoint, params=params)

    def get_klines(self, symbol, interval="5m", limit=1000, start_time=None, end_time=None):
        """Fetch a single page of klines (max 1000)."""
        endpoint = "/fapi/v1/klines" if self.product == "futures" else "/api/v3/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": min(1000, int(limit))}
        if start_time:
            params["startTime"] = int(start_time)
        if end_time:
            params["endTime"] = int(end_time)
        return self.request("GET", endpoint, params=params)

    def get_klines_paginated(self, symbol, interval="15m", days=60, end_time=None):
        """Dynamically paginate backward using endTime to fetch full historical range."""
        interval_ms = INTERVAL_MS.get(interval)
        if not interval_ms:
            raise ValueError("Unsupported interval: %s" % interval)

        now_ms = int(time.time() * 1000)
        target_end_ms = int(end_time) if end_time else now_ms
        target_start_ms = target_end_ms - (days * 24 * 60 * 60 * 1000)

        all_klines = []
        curr_end_ms = target_end_ms
        page_limit = 1000

        prev_earliest = None
        while curr_end_ms > target_start_ms:
            page = self.get_klines(
                symbol=symbol,
                interval=interval,
                limit=page_limit,
                start_time=target_start_ms,
                end_time=curr_end_ms
            )
            if not page or not isinstance(page, list) or len(page) == 0:
                break

            all_klines.extend(page)
            earliest_open_time = int(page[0][0])
            if earliest_open_time <= target_start_ms or earliest_open_time == prev_earliest or len(page) < 2:
                break
            prev_earliest = earliest_open_time
            curr_end_ms = earliest_open_time - 1

        # Deduplicate and sort strictly ascending by open time
        unique_by_time = {}
        for row in all_klines:
            unique_by_time[int(row[0])] = row
        sorted_rows = [unique_by_time[k] for k in sorted(unique_by_time)]

        # Drop the last candle if it is still forming / unclosed
        if sorted_rows and int(sorted_rows[-1][6]) > now_ms:
            sorted_rows.pop()

        # Check timestamp continuity and detect gaps
        gaps = []
        for i in range(1, len(sorted_rows)):
            expected = int(sorted_rows[i - 1][0]) + interval_ms
            actual = int(sorted_rows[i][0])
            if actual > expected:
                missing_count = (actual - expected) // interval_ms
                gaps.append({"expected": expected, "actual": actual, "missing_candles": missing_count})

        return {
            "symbol": symbol.upper(),
            "interval": interval,
            "days_requested": days,
            "candle_count": len(sorted_rows),
            "start_time": sorted_rows[0][0] if sorted_rows else None,
            "end_time": sorted_rows[-1][6] if sorted_rows else None,
            "gaps": gaps,
            "klines": sorted_rows
        }

    # --- Account & Orders (Authenticated) ---

    def get_account(self):
        """Get account balances and permissions."""
        if self.product == "futures":
            return self.request("GET", "/fapi/v2/account", signed=True)
        return self.request("GET", "/api/v3/account", signed=True)

    def get_position_mode(self):
        """For USD(S)-M Futures: Query One-Way vs. Hedge mode."""
        if self.product != "futures":
            return {"dualSidePosition": False}
        return self.request("GET", "/fapi/v1/positionSide/dual", signed=True)

    def get_order(self, symbol, order_id=None, orig_client_order_id=None):
        """Query standard order status by ID or client order ID."""
        endpoint = "/fapi/v1/order" if self.product == "futures" else "/api/v3/order"
        params = {"symbol": symbol.upper()}
        if order_id:
            params["orderId"] = order_id
        if orig_client_order_id:
            params["origClientOrderId"] = orig_client_order_id
        return self.request("GET", endpoint, params=params, signed=True)

    def create_order(self, symbol, side, order_type, quantity=None, price=None,
                     client_order_id=None, time_in_force=None, **extra):
        """Submit a standard order (Spot or Futures)."""
        endpoint = "/fapi/v1/order" if self.product == "futures" else "/api/v3/order"
        params = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
        }
        if quantity is not None:
            params["quantity"] = str(quantity)
        if price is not None:
            params["price"] = str(price)
        if client_order_id:
            field = "newClientOrderId"
            params[field] = client_order_id
        if time_in_force:
            params["timeInForce"] = time_in_force
        params.update(extra)
        return self.request("POST", endpoint, params=params, signed=True)

    def create_spot_oco(self, symbol, side, quantity, price, stop_price, stop_limit_price=None,
                        list_client_order_id=None, limit_client_order_id=None, stop_client_order_id=None):
        """Spot OCO order: Uses modern POST /api/v3/orderList/oco."""
        if self.product != "spot":
            raise BinanceAPIError("OCO order lists are only available for Spot trading")
        params = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "quantity": str(quantity),
            "aboveType": "LIMIT_MAKER",
            "abovePrice": str(price),
            "belowType": "STOP_LOSS_LIMIT" if stop_limit_price else "STOP_LOSS",
            "belowStopPrice": str(stop_price),
        }
        if stop_limit_price:
            params["belowPrice"] = str(stop_limit_price)
            params["belowTimeInForce"] = "GTC"
        if list_client_order_id:
            params["listClientOrderId"] = list_client_order_id
        if limit_client_order_id:
            params["aboveClientOrderId"] = limit_client_order_id
        if stop_client_order_id:
            params["belowClientOrderId"] = stop_client_order_id
        return self.request("POST", "/api/v3/orderList/oco", params=params, signed=True)

    def create_futures_algo_order(self, symbol, side, algo_type, trigger_price,
                                  quantity=None, client_algo_id=None, reduce_only=True, **extra):
        """USD(S)-M Futures Conditional Order via Algo Service (/fapi/v1/algoOrder)."""
        if self.product != "futures":
            raise BinanceAPIError("Algo orders are only available for Futures trading")
        params = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "algoType": algo_type.upper(),  # STOP_MARKET, TAKE_PROFIT_MARKET, etc.
            "triggerPrice": str(trigger_price),
            "reduceOnly": "true" if reduce_only else "false",
        }
        if quantity is not None:
            params["quantity"] = str(quantity)
        if client_algo_id:
            params["clientAlgoId"] = client_algo_id
        params.update(extra)
        return self.request("POST", "/fapi/v1/algoOrder", params=params, signed=True)

    def cancel_order(self, symbol, order_id=None, orig_client_order_id=None):
        endpoint = "/fapi/v1/order" if self.product == "futures" else "/api/v3/order"
        params = {"symbol": symbol.upper()}
        if order_id:
            params["orderId"] = order_id
        if orig_client_order_id:
            params["origClientOrderId"] = orig_client_order_id
        return self.request("DELETE", endpoint, params=params, signed=True)


# --- Strict Decimal Exchange Filter Parsing & Quantization ---

def parse_exchange_filters(symbol_info):
    """Extract tickSize, stepSize, minQty, and notional requirements as Decimals."""
    filters = {
        "tick_size": Decimal("0.00000001"),
        "min_price": Decimal("0"),
        "max_price": Decimal("999999999"),
        "step_size": Decimal("0.00000001"),
        "min_qty": Decimal("0"),
        "max_qty": Decimal("999999999"),
        "min_notional": Decimal("5.0"),
    }
    for f in symbol_info.get("filters", []):
        ftype = f.get("filterType")
        if ftype == "PRICE_FILTER":
            if "tickSize" in f and Decimal(f["tickSize"]) > 0:
                filters["tick_size"] = Decimal(f["tickSize"])
            if "minPrice" in f:
                filters["min_price"] = Decimal(f["minPrice"])
            if "maxPrice" in f:
                filters["max_price"] = Decimal(f["maxPrice"])
        elif ftype in ("LOT_SIZE", "MARKET_LOT_SIZE"):
            if "stepSize" in f and Decimal(f["stepSize"]) > 0:
                filters["step_size"] = Decimal(f["stepSize"])
            if "minQty" in f:
                filters["min_qty"] = Decimal(f["minQty"])
            if "maxQty" in f:
                filters["max_qty"] = Decimal(f["maxQty"])
        elif ftype in ("NOTIONAL", "MIN_NOTIONAL"):
            if "minNotional" in f and Decimal(f["minNotional"]) > 0:
                filters["min_notional"] = Decimal(f["minNotional"])
            elif "notional" in f and Decimal(f["notional"]) > 0:
                filters["min_notional"] = Decimal(f["notional"])
    return filters


def quantize_price(price, tick_size, min_price=Decimal("0")):
    """Quantize price as a mathematically valid multiple of tickSize."""
    p = Decimal(str(price))
    ts = Decimal(str(tick_size))
    mp = Decimal(str(min_price))
    if ts <= Decimal("0"):
        return p
    steps = ((p - mp) // ts)
    return (steps * ts) + mp


def quantize_quantity(qty, step_size, min_qty=Decimal("0")):
    """Quantize quantity rounded down to the valid multiple of stepSize."""
    q = Decimal(str(qty))
    ss = Decimal(str(step_size))
    mq = Decimal(str(min_qty))
    if ss <= Decimal("0"):
        return q
    if q < mq:
        return Decimal("0")
    steps = ((q - mq) // ss)
    return (steps * ss) + mq


def validate_order(symbol, side, price, quantity, filters):
    """Validate order parameters against Binance filters using Decimal."""
    p = Decimal(str(price)) if price is not None else None
    q = Decimal(str(quantity))
    errors = []

    q_quant = quantize_quantity(q, filters["step_size"], filters["min_qty"])
    if q < filters["min_qty"] or q_quant < filters["min_qty"]:
        errors.append("Quantity %s is below minimum LOT_SIZE %s" % (q, filters["min_qty"]))
    if q_quant > filters["max_qty"]:
        errors.append("Quantity %s exceeds maximum LOT_SIZE %s" % (q_quant, filters["max_qty"]))

    p_quant = None
    notional = None
    if p is not None:
        p_quant = quantize_price(p, filters["tick_size"], filters["min_price"])
        if p_quant < filters["min_price"]:
            errors.append("Price %s is below PRICE_FILTER minPrice %s" % (p_quant, filters["min_price"]))
        if p_quant > filters["max_price"]:
            errors.append("Price %s exceeds PRICE_FILTER maxPrice %s" % (p_quant, filters["max_price"]))
        notional = p_quant * q_quant
        if notional < filters["min_notional"]:
            errors.append("Order notional $%s is below MIN_NOTIONAL $%s" % (notional, filters["min_notional"]))

    return {
        "valid": len(errors) == 0,
        "symbol": symbol.upper(),
        "side": side.upper(),
        "original_price": p,
        "quantized_price": p_quant,
        "original_qty": q,
        "quantized_qty": q_quant,
        "notional": notional,
        "errors": errors
    }

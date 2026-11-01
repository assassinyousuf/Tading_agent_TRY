#!/usr/bin/env python3
"""Binance REST API Client for Spot and USD(S)-M Futures.

Pure Python 3.8+ standard library. Zero external pip dependencies.
Implements:
- Spot and USD(S)-M Futures REST endpoints (Live & Testnet)
- Strict Decimal arithmetic for LOT_SIZE, PRICE_FILTER, and NOTIONAL
- Immutable HMAC-SHA256 signature generation with pre-signing percent-encoding
- Dynamic backward klines pagination with gap detection & window splitting
- Proactive request weight tracking and 429/418 Retry-After handling
- Idempotent order reconciliation and Algo order support
- Write/read request isolation preventing blind retries on order submissions
"""

import datetime
import decimal
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import hashlib
import hmac
import json
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Prefer IPv4 to avoid multi-second IPv6 fallback timeouts on Windows networks
try:
    _orig_getaddrinfo = socket.getaddrinfo
    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    socket.getaddrinfo = _ipv4_getaddrinfo
except Exception:
    pass

logger = logging.getLogger("binance_client")

# API Host URLs (4 explicit environments)
SPOT_LIVE_HOST = "https://api.binance.com"
SPOT_TESTNET_HOST = "https://testnet.binance.vision"
FUTURES_LIVE_HOST = "https://fapi.binance.com"
FUTURES_TESTNET_HOST = "https://testnet.binancefuture.com"

SPOT_PROD_URL = SPOT_LIVE_HOST
SPOT_TESTNET_URL = SPOT_TESTNET_HOST
FUTURES_PROD_URL = FUTURES_LIVE_HOST
FUTURES_TESTNET_URL = FUTURES_TESTNET_HOST

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"

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


class BinanceRateLimitError(BinanceAPIError):
    """Raised when rate limits (429/418) or weight limits are breached."""
    pass


class BinanceAmbiguousError(BinanceAPIError):
    """Raised when a state-mutating request (order, cancel) fails ambiguously (timeout or 5xx).
    Callers MUST reconcile state via GET before attempting any resubmission.
    """
    def __init__(self, message, client_order_id=None, symbol=None, endpoint=None, payload=None, **kwargs):
        super().__init__(message, **kwargs)
        self.client_order_id = client_order_id
        self.symbol = symbol
        self.endpoint = endpoint
        self.payload = payload


class BinanceClient:
    """Production-grade Binance REST API Client for Spot and USD(S)-M Futures."""

    def __init__(self, api_key=None, api_secret=None, product="spot", testnet=False,
                 timeout=15, weight_limit=1200, weight_throttle_ratio=0.80):
        self.product = product.lower()
        if self.product not in ("spot", "futures"):
            raise ValueError("product must be 'spot' or 'futures', got %r" % product)
        self.testnet = bool(testnet)
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("BINANCE_SECRET_KEY", "")
        self.used_weight_1m = 0
        self.weight_limit = weight_limit
        self.weight_throttle_ratio = weight_throttle_ratio
        self.time_offset_ms = 0
        self.base_url = self._resolve_base_url()
        self.order_count_headers = {}

    def _resolve_base_url(self):
        if self.product == "futures":
            return FUTURES_TESTNET_HOST if self.testnet else FUTURES_LIVE_HOST
        return SPOT_TESTNET_HOST if self.testnet else SPOT_LIVE_HOST

    def __repr__(self):
        masked_key = (self.api_key[:4] + "..." + self.api_key[-4:]) if len(self.api_key) > 8 else ("***" if self.api_key else "None")
        return "<BinanceClient product=%s testnet=%s base_url=%s api_key=%s api_secret=***>" % (
            self.product, self.testnet, self.base_url, masked_key
        )

    def set_time_offset(self, server_time_ms):
        """Calibrate local clock to Binance server time."""
        local_ms = int(time.time() * 1000)
        self.time_offset_ms = server_time_ms - local_ms

    def get_timestamp(self):
        """Current timestamp in ms adjusted for server time offset."""
        return int(time.time() * 1000) + self.time_offset_ms

    def build_signed_query(self, params):
        """Build an immutable HMAC-SHA256 signed query string.

        Specification:
        1. Build the ordered parameter list once, including timestamp and recvWindow.
        2. Percent-encode the parameters ONCE into an immutable ASCII query string:
           encoded_query = urllib.parse.urlencode(params, safe='')
        3. Compute the HMAC-SHA256 hex digest over exact encoded_query.encode('utf-8') bytes.
        4. Append &signature={sig} to that exact query string without reordering or mutating.
        """
        if not self.api_secret:
            raise BinanceAPIError("API secret required for authenticated requests")

        # Convert dict or list of tuples into list of tuples with timestamp & recvWindow
        if isinstance(params, dict):
            items = list(params.items())
        else:
            items = list(params)

        param_dict = dict(items)
        if "timestamp" not in param_dict:
            items.append(("timestamp", str(self.get_timestamp())))
        if "recvWindow" not in param_dict:
            items.append(("recvWindow", "5000"))

        # Stringify all values cleanly
        clean_items = [(str(k), str(v)) for k, v in items]

        # 1. Percent-encode parameters ONCE
        encoded_query = urllib.parse.urlencode(clean_items, safe='')

        # 2. Compute HMAC-SHA256 hex digest over exact encoded_query bytes
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            encoded_query.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        # 3. Append &signature={sig} to that exact encoded_query string
        final_query = "%s&signature=%s" % (encoded_query, signature)
        return final_query, signature

    def sign_params(self, params):
        """Legacy helper returning dict with signature key for compatibility."""
        p = dict(params)
        if "timestamp" not in p:
            p["timestamp"] = self.get_timestamp()
        if "recvWindow" not in p:
            p["recvWindow"] = 5000
        # Convert all to strings or ints
        p_clean = {k: str(v) for k, v in p.items()}
        encoded_query = urllib.parse.urlencode(p_clean)
        signature = hmac.new(
            self.api_secret.encode("utf-8") if self.api_secret else b"",
            encoded_query.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        p["signature"] = signature
        return p

    def check_rate_limit_throttle(self):
        """Proactively throttle if used weight exceeds configured threshold ratio (default 80%)."""
        threshold = int(self.weight_limit * self.weight_throttle_ratio)
        if self.used_weight_1m >= threshold:
            sleep_sec = 2.0
            logger.warning("Proactive rate limit throttle: used_weight_1m=%d >= threshold=%d. Sleeping %.1fs",
                           self.used_weight_1m, threshold, sleep_sec)
            time.sleep(sleep_sec)

    def request(self, method, endpoint, params=None, signed=False, is_write=None, max_retries=3):
        """Execute HTTP request with strict write/read isolation and rate-limit governance."""
        method_upper = method.upper()
        if is_write is None:
            is_write = method_upper in ("POST", "DELETE", "PUT")

        self.check_rate_limit_throttle()

        headers = {"User-Agent": UA, "Accept": "application/json"}
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        url = self.base_url + endpoint
        data = None

        if signed:
            final_query, _ = self.build_signed_query(params or {})
            if method_upper in ("GET", "DELETE"):
                url += "?" + final_query
            else:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                data = final_query.encode("utf-8")
        else:
            if params:
                encoded_query = urllib.parse.urlencode(params)
                if method_upper in ("GET", "DELETE"):
                    url += "?" + encoded_query
                else:
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                    data = encoded_query.encode("utf-8")

        # Determine retry budget: READ requests get max_retries on 5xx/timeout.
        # WRITE requests get exactly 1 attempt; on 5xx or timeout, an ambiguous error is raised.
        attempts_allowed = 1 if is_write else max_retries

        for attempt in range(1, attempts_allowed + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method_upper)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    # Update rate limit weight and order count headers
                    weight_header = resp.headers.get("x-mbx-used-weight-1m")
                    if weight_header:
                        try:
                            self.used_weight_1m = int(weight_header)
                        except ValueError:
                            pass
                    for hdr_name, hdr_val in resp.headers.items():
                        if hdr_name.lower().startswith("x-mbx-order-count-"):
                            self.order_count_headers[hdr_name.lower()] = hdr_val

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

                # Inspect weight header even on error
                weight_header = e.headers.get("x-mbx-used-weight-1m")
                if weight_header:
                    try:
                        self.used_weight_1m = int(weight_header)
                    except ValueError:
                        pass

                # Handle 429 Too Many Requests and 418 IP Auto-Ban
                if status_code in (429, 418):
                    retry_after = e.headers.get("Retry-After")
                    sleep_time = int(retry_after) if retry_after and retry_after.isdigit() else (attempt * 3.0)
                    logger.warning("Binance rate limit %d on %s, sleeping for %ds (Retry-After)",
                                   status_code, endpoint, sleep_time)
                    time.sleep(sleep_time)
                    if attempt >= attempts_allowed:
                        raise BinanceRateLimitError(
                            "Rate limit exceeded (%d): %s" % (status_code, parsed_msg),
                            code=parsed_code, status_code=status_code, response_body=error_body
                        )
                    continue

                # 5xx Server Errors
                if status_code >= 500:
                    if is_write:
                        # Ambiguous outcome! Order might have matched before server crashed
                        cid = (dict(params or {})).get("newClientOrderId") or (dict(params or {})).get("listClientOrderId") or (dict(params or {})).get("clientAlgoId")
                        sym = (dict(params or {})).get("symbol")
                        raise BinanceAmbiguousError(
                            "HTTP %d on write request to %s: %s" % (status_code, endpoint, parsed_msg),
                            code=parsed_code, status_code=status_code, response_body=error_body,
                            client_order_id=cid, symbol=sym, endpoint=endpoint, payload=params
                        )
                    if attempt < attempts_allowed:
                        time.sleep(attempt * 1.0)
                        continue

                raise BinanceAPIError(
                    "Binance API error %d on %s: %s" % (status_code, endpoint, parsed_msg),
                    code=parsed_code, status_code=status_code, response_body=error_body
                )

            except (urllib.error.URLError, TimeoutError, OSError, socket.timeout) as e:
                if is_write:
                    # Timeout / Network error on write request is AMBIGUOUS
                    cid = (dict(params or {})).get("newClientOrderId") or (dict(params or {})).get("listClientOrderId") or (dict(params or {})).get("clientAlgoId")
                    sym = (dict(params or {})).get("symbol")
                    raise BinanceAmbiguousError(
                        "Network timeout or error on write request to %s: %s" % (url, e),
                        client_order_id=cid, symbol=sym, endpoint=endpoint, payload=params
                    )
                if attempt < attempts_allowed:
                    time.sleep(attempt * 1.0)
                    continue
                raise BinanceAPIError("Network connection error to %s: %s" % (url, e))

    # =========================================================================
    # MARKET DATA ENDPOINTS (PUBLIC)
    # =========================================================================

    def get_time(self):
        """Fetch Binance server time and calibrate time offset.

        Spot Endpoint:    GET /api/v3/time
        Futures Endpoint: GET /fapi/v1/time
        Counterpart:      None (read-only)
        """
        endpoint = "/fapi/v1/time" if self.product == "futures" else "/api/v3/time"
        res = self.request("GET", endpoint)
        server_time = res.get("serverTime")
        if server_time:
            self.set_time_offset(int(server_time))
        return server_time

    def get_exchange_info(self, symbol=None):
        """Fetch exchange trading rules, symbol filters, and asset statuses.

        Spot Endpoint:    GET /api/v3/exchangeInfo
        Futures Endpoint: GET /fapi/v1/exchangeInfo
        Parameters:       symbol (optional, e.g. 'SOLUSDT')
        Counterpart:      None (read-only)
        """
        endpoint = "/fapi/v1/exchangeInfo" if self.product == "futures" else "/api/v3/exchangeInfo"
        params = {"symbol": symbol.upper()} if symbol else None
        return self.request("GET", endpoint, params=params)

    def get_ticker_24hr(self, symbol=None):
        """Fetch 24-hour ticker volume, price change, and statistics.

        Spot Endpoint:    GET /api/v3/ticker/24hr
        Futures Endpoint: GET /fapi/v1/ticker/24hr
        Parameters:       symbol (optional)
        Counterpart:      None (read-only)
        """
        endpoint = "/fapi/v1/ticker/24hr" if self.product == "futures" else "/api/v3/ticker/24hr"
        params = {"symbol": symbol.upper()} if symbol else None
        return self.request("GET", endpoint, params=params)

    def get_klines(self, symbol, interval="5m", limit=1000, start_time=None, end_time=None):
        """Fetch a single page of historical klines / candlestick bars (max 1000).

        Spot Endpoint:    GET /api/v3/klines
        Futures Endpoint: GET /fapi/v1/klines
        Parameters:       symbol, interval, limit (<=1000), startTime (ms), endTime (ms)
        Counterpart:      None (read-only)
        """
        endpoint = "/fapi/v1/klines" if self.product == "futures" else "/api/v3/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": min(1000, int(limit))}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        return self.request("GET", endpoint, params=params)

    def get_klines_backward_paginated(self, symbol, interval="15m", days=60, end_time=None):
        """Dynamically paginate backward using endTime = earliest_open_time - 1 until startTime is reached.

        Specification Requirements:
        1. Calculate exact required candles from interval_ms and days.
        2. Paginate backward using endTime = earliest_open_time - 1 until requested startTime is reached.
        3. Deduplicate and sort candles in strictly ascending order, excluding unclosed candle.
        4. Check timestamp continuity (t[i] - t[i-1] == interval_ms). Never fabricate missing candles.
        5. Record every gap segment and return data integrity summary.
        """
        interval_ms = INTERVAL_MS.get(interval)
        if not interval_ms:
            raise ValueError("Unsupported interval: %s" % interval)

        now_ms = int(time.time() * 1000)
        target_end_ms = int(end_time) if end_time else now_ms
        target_start_ms = target_end_ms - (days * 24 * 60 * 60 * 1000)
        total_expected_candles = (target_end_ms - target_start_ms) // interval_ms

        all_klines = []
        curr_end_ms = target_end_ms
        page_limit = 1000
        prev_earliest = None

        while curr_end_ms > target_start_ms:
            page = self.get_klines(
                symbol=symbol,
                interval=interval,
                limit=page_limit,
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
            t = int(row[0])
            if t >= target_start_ms:
                unique_by_time[t] = row
        sorted_rows = [unique_by_time[k] for k in sorted(unique_by_time)]

        # Drop the last candle if it is still forming / unclosed
        if sorted_rows and int(sorted_rows[-1][6]) > target_end_ms:
            sorted_rows.pop()

        # Check timestamp continuity and detect gaps
        gaps = detect_gaps(sorted_rows, interval_ms)
        missing_candles_count = sum(g["missing_candles"] for g in gaps)
        missing_duration_ms = sum(g["missing_duration_ms"] for g in gaps)

        data_integrity = {
            "requested_range_start": target_start_ms,
            "requested_range_end": target_end_ms,
            "actual_range_start": sorted_rows[0][0] if sorted_rows else None,
            "actual_range_end": sorted_rows[-1][6] if sorted_rows else None,
            "total_expected_candles": total_expected_candles,
            "total_candles_fetched": len(sorted_rows),
            "gap_count": len(gaps),
            "total_missing_candles": missing_candles_count,
            "total_missing_duration_ms": missing_duration_ms,
            "gaps": gaps
        }

        return {
            "symbol": symbol.upper(),
            "interval": interval,
            "days_requested": days,
            "candle_count": len(sorted_rows),
            "start_time": sorted_rows[0][0] if sorted_rows else None,
            "end_time": sorted_rows[-1][6] if sorted_rows else None,
            "data_integrity": data_integrity,
            "gaps": gaps,
            "klines": sorted_rows
        }

    def get_klines_paginated(self, symbol, interval="15m", days=60, end_time=None):
        """Alias for backward pagination."""
        return self.get_klines_backward_paginated(symbol, interval, days, end_time)

    # =========================================================================
    # ACCOUNT & POSITION ENDPOINTS (AUTHENTICATED)
    # =========================================================================

    def get_account(self):
        """Fetch account balances, permissions, and status.

        Spot Endpoint:    GET /api/v3/account (Signed)
        Futures Endpoint: GET /fapi/v2/account (Signed)
        Counterpart:      None (read-only)
        """
        if self.product == "futures":
            return self.request("GET", "/fapi/v2/account", signed=True)
        return self.request("GET", "/api/v3/account", signed=True)

    def get_position_risk(self, symbol=None):
        """Fetch real-time position risk and exposure for USD(S)-M Futures.

        Futures Endpoint: GET /fapi/v2/positionRisk (Signed)
        Parameters:       symbol (optional, e.g. 'SOLUSDT')
        Returns:          list of positions with positionAmt, entryPrice, unRealizedProfit, positionSide
        Counterpart:      None (read-only)
        """
        if self.product != "futures":
            raise BinanceAPIError("Position risk is only applicable to USD(S)-M Futures")
        params = {"symbol": symbol.upper()} if symbol else None
        return self.request("GET", "/fapi/v2/positionRisk", params=params, signed=True)

    def get_position_mode(self):
        """Query USD(S)-M Futures Position Side Mode (One-Way vs. Hedge).

        Futures Endpoint: GET /fapi/v1/positionSide/dual (Signed)
        Returns:          {"dualSidePosition": True} (Hedge) or {"dualSidePosition": False} (One-Way)
        Counterpart:      POST /fapi/v1/positionSide/dual
        """
        if self.product != "futures":
            return {"dualSidePosition": False}
        return self.request("GET", "/fapi/v1/positionSide/dual", signed=True)

    # =========================================================================
    # STANDARD ORDERS (SPOT & FUTURES)
    # =========================================================================

    def create_order(self, symbol, side, order_type, quantity=None, price=None,
                     stop_price=None, new_client_order_id=None, client_order_id=None,
                     time_in_force=None, reduce_only=None, position_side=None, **extra):
        """Submit a single order on Binance Spot or USD(S)-M Futures.

        Spot Endpoint:    POST /api/v3/order (Signed)
        Futures Endpoint: POST /fapi/v1/order (Signed)
        Required:         symbol, side (BUY/SELL), type (LIMIT, MARKET, etc.)
        Identifiers:      newClientOrderId (<=36 chars)
        Query Endpoint:   GET /api/v3/order or GET /fapi/v1/order (by origClientOrderId or orderId)
        Cancel Endpoint:  DELETE /api/v3/order or DELETE /fapi/v1/order (by origClientOrderId or orderId)
        Supported Types:  LIMIT, MARKET, STOP_LOSS, STOP_LOSS_LIMIT, TAKE_PROFIT, TAKE_PROFIT_LIMIT, LIMIT_MAKER, STOP_MARKET, TAKE_PROFIT_MARKET
        """
        endpoint = "/fapi/v1/order" if self.product == "futures" else "/api/v3/order"
        cid = new_client_order_id or client_order_id or extra.pop("client_order_id", None)
        params = [
            ("symbol", symbol.upper()),
            ("side", side.upper()),
            ("type", order_type.upper()),
        ]
        if quantity is not None:
            params.append(("quantity", str(quantity)))
        if price is not None:
            params.append(("price", str(price)))
        sp = stop_price or extra.pop("stop_price", None) or extra.pop("stopPrice", None)
        if sp is not None:
            params.append(("stopPrice", str(sp)))
        if cid:
            params.append(("newClientOrderId", str(cid)))
        tif = time_in_force or extra.pop("time_in_force", None) or extra.pop("timeInForce", None)
        if tif:
            params.append(("timeInForce", str(tif)))
        if self.product == "futures":
            ro = reduce_only if reduce_only is not None else extra.pop("reduce_only", None)
            if ro is not None:
                params.append(("reduceOnly", "true" if ro else "false"))
            ps = position_side or extra.pop("position_side", None) or extra.pop("positionSide", None)
            if ps:
                params.append(("positionSide", str(ps).upper()))
            cp = extra.pop("close_position", None) or extra.pop("closePosition", None)
            if cp is not None:
                params.append(("closePosition", "true" if cp else "false"))

        for k, v in extra.items():
            if v is not None:
                params.append((str(k), str(v)))

        return self.request("POST", endpoint, params=params, signed=True, is_write=True)

    def get_order(self, symbol, order_id=None, orig_client_order_id=None):
        """Query standard order status.

        Spot Endpoint:    GET /api/v3/order (Signed)
        Futures Endpoint: GET /fapi/v1/order (Signed)
        Identifiers:      orderId OR origClientOrderId
        """
        endpoint = "/fapi/v1/order" if self.product == "futures" else "/api/v3/order"
        params = [("symbol", symbol.upper())]
        if order_id:
            params.append(("orderId", str(order_id)))
        if orig_client_order_id:
            params.append(("origClientOrderId", str(orig_client_order_id)))
        return self.request("GET", endpoint, params=params, signed=True)

    def cancel_order(self, symbol, order_id=None, orig_client_order_id=None):
        """Cancel an active standard order.

        Spot Endpoint:    DELETE /api/v3/order (Signed)
        Futures Endpoint: DELETE /fapi/v1/order (Signed)
        Identifiers:      orderId OR origClientOrderId
        """
        endpoint = "/fapi/v1/order" if self.product == "futures" else "/api/v3/order"
        params = [("symbol", symbol.upper())]
        if order_id:
            params.append(("orderId", str(order_id)))
        if orig_client_order_id:
            params.append(("origClientOrderId", str(orig_client_order_id)))
        return self.request("DELETE", endpoint, params=params, signed=True, is_write=True)

    def get_open_orders(self, symbol=None):
        """Query all currently open standard orders."""
        endpoint = "/fapi/v1/openOrders" if self.product == "futures" else "/api/v3/openOrders"
        params = [("symbol", symbol.upper())] if symbol else None
        return self.request("GET", endpoint, params=params, signed=True)

    def cancel_all_open_orders(self, symbol):
        """Cancel all open standard orders for a symbol."""
        endpoint = "/fapi/v1/allOpenOrders" if self.product == "futures" else "/api/v3/openOrders"
        params = [("symbol", symbol.upper())]
        return self.request("DELETE", endpoint, params=params, signed=True, is_write=True)

    def get_my_trades(self, symbol, order_id=None, start_time=None, end_time=None, from_id=None, limit=None):
        """Query execution trades and fill fees for account.

        Spot Endpoint:    GET /api/v3/myTrades (Signed)
        Futures Endpoint: GET /fapi/v1/userTrades (Signed)
        Identifiers:      symbol, orderId
        """
        endpoint = "/fapi/v1/userTrades" if self.product == "futures" else "/api/v3/myTrades"
        params = [("symbol", symbol.upper())]
        if order_id is not None:
            params.append(("orderId", str(order_id)))
        if start_time is not None:
            params.append(("startTime", str(start_time)))
        if end_time is not None:
            params.append(("endTime", str(end_time)))
        if from_id is not None:
            params.append(("fromId", str(from_id)))
        if limit is not None:
            params.append(("limit", str(limit)))
        return self.request("GET", endpoint, params=params, signed=True)

    # =========================================================================
    # SPOT ORDER LISTS (OCO & OTOCO)
    # =========================================================================

    def create_spot_oco(self, symbol, side, quantity, price, stop_price, stop_limit_price=None,
                        list_client_order_id=None, limit_client_order_id=None, stop_client_order_id=None):
        """Submit Spot One-Cancels-the-Other (OCO) order list.

        Endpoint:        POST /api/v3/orderList/oco (Signed)
        Required:        symbol, side (SELL/BUY), quantity, aboveType, abovePrice, belowType, belowStopPrice
        Identifiers:     listClientOrderId (<=36 chars), aboveClientOrderId, belowClientOrderId
        Query Endpoint:  GET /api/v3/orderList (by orderListId or origClientOrderId)
        Cancel Endpoint: DELETE /api/v3/orderList (by orderListId or listClientOrderId)
        """
        if self.product != "spot":
            raise BinanceAPIError("OCO order lists are only available for Spot trading")

        side_u = side.upper()
        if side_u == "SELL":
            # For SELL: Take-profit is above market, stop-loss is below market
            params = [
                ("symbol", symbol.upper()),
                ("side", side_u),
                ("quantity", str(quantity)),
                ("aboveType", "LIMIT_MAKER"),
                ("abovePrice", str(price)),
                ("belowType", "STOP_LOSS_LIMIT" if stop_limit_price else "STOP_LOSS"),
                ("belowStopPrice", str(stop_price)),
            ]
            if stop_limit_price:
                params.append(("belowPrice", str(stop_limit_price)))
                params.append(("belowTimeInForce", "GTC"))
            if limit_client_order_id:
                params.append(("aboveClientOrderId", str(limit_client_order_id)))
            if stop_client_order_id:
                params.append(("belowClientOrderId", str(stop_client_order_id)))
        else:
            # For BUY: Stop-loss is above market, take-profit is below market
            params = [
                ("symbol", symbol.upper()),
                ("side", side_u),
                ("quantity", str(quantity)),
                ("aboveType", "STOP_LOSS_LIMIT" if stop_limit_price else "STOP_LOSS"),
                ("aboveStopPrice", str(stop_price)),
                ("belowType", "LIMIT_MAKER"),
                ("belowPrice", str(price)),
            ]
            if stop_limit_price:
                params.append(("abovePrice", str(stop_limit_price)))
                params.append(("aboveTimeInForce", "GTC"))
            if stop_client_order_id:
                params.append(("aboveClientOrderId", str(stop_client_order_id)))
            if limit_client_order_id:
                params.append(("belowClientOrderId", str(limit_client_order_id)))

        if list_client_order_id:
            params.append(("listClientOrderId", str(list_client_order_id)))

        return self.request("POST", "/api/v3/orderList/oco", params=params, signed=True, is_write=True)

    def create_spot_otoco(self, symbol, working_side, working_price, working_qty,
                          pending_side, pending_qty, pending_above_price, pending_below_stop_price,
                          pending_below_price=None, list_client_order_id=None,
                          working_client_order_id=None, pending_above_client_id=None,
                          pending_below_client_id=None):
        """Submit Spot One-Triggers-One-Cancels-the-Other (OTOCO) order list.

        Endpoint:        POST /api/v3/orderList/otoco (Signed)
        Behavior:        Places a working order (e.g. LIMIT BUY). Once the working order completely fills,
                         Binance automatically activates the pending OCO protective exit pair.
        Identifiers:     listClientOrderId (<=36 chars), workingClientOrderId, pendingAboveClientOrderId, pendingBelowClientOrderId
        Query Endpoint:  GET /api/v3/orderList
        Cancel Endpoint: DELETE /api/v3/orderList
        """
        if self.product != "spot":
            raise BinanceAPIError("OTOCO order lists are only available for Spot trading")

        pside = pending_side.upper()
        params = [
            ("symbol", symbol.upper()),
            ("workingType", "LIMIT"),
            ("workingSide", working_side.upper()),
            ("workingPrice", str(working_price)),
            ("workingQuantity", str(working_qty)),
            ("workingTimeInForce", "GTC"),
            ("pendingSide", pside),
            ("pendingQuantity", str(pending_qty)),
        ]
        if pside == "SELL":
            params.extend([
                ("pendingAboveType", "LIMIT_MAKER"),
                ("pendingAbovePrice", str(pending_above_price)),
                ("pendingBelowType", "STOP_LOSS_LIMIT" if pending_below_price else "STOP_LOSS"),
                ("pendingBelowStopPrice", str(pending_below_stop_price)),
            ])
            if pending_below_price:
                params.append(("pendingBelowPrice", str(pending_below_price)))
                params.append(("pendingBelowTimeInForce", "GTC"))
            if pending_above_client_id:
                params.append(("pendingAboveClientOrderId", str(pending_above_client_id)))
            if pending_below_client_id:
                params.append(("pendingBelowClientOrderId", str(pending_below_client_id)))
        else:
            params.extend([
                ("pendingAboveType", "STOP_LOSS_LIMIT" if pending_below_price else "STOP_LOSS"),
                ("pendingAboveStopPrice", str(pending_below_stop_price)),
                ("pendingBelowType", "LIMIT_MAKER"),
                ("pendingBelowPrice", str(pending_above_price)),
            ])
            if pending_below_price:
                params.append(("pendingAbovePrice", str(pending_below_price)))
                params.append(("pendingAboveTimeInForce", "GTC"))
            if pending_below_client_id:
                params.append(("pendingAboveClientOrderId", str(pending_below_client_id)))
            if pending_above_client_id:
                params.append(("pendingBelowClientOrderId", str(pending_above_client_id)))

        if list_client_order_id:
            params.append(("listClientOrderId", str(list_client_order_id)))
        if working_client_order_id:
            params.append(("workingClientOrderId", str(working_client_order_id)))

        return self.request("POST", "/api/v3/orderList/otoco", params=params, signed=True, is_write=True)

    def get_spot_order_list(self, order_list_id=None, orig_client_order_id=None):
        """Query status of Spot OCO or OTOCO order list.

        Endpoint:    GET /api/v3/orderList (Signed)
        Identifiers: orderListId OR origClientOrderId
        """
        if self.product != "spot":
            raise BinanceAPIError("Order lists are only available for Spot trading")
        params = []
        if order_list_id:
            params.append(("orderListId", str(order_list_id)))
        if orig_client_order_id:
            params.append(("origClientOrderId", str(orig_client_order_id)))
        return self.request("GET", "/api/v3/orderList", params=params, signed=True)

    def cancel_spot_order_list(self, symbol, order_list_id=None, list_client_order_id=None):
        """Cancel an active Spot OCO or OTOCO order list.

        Endpoint:    DELETE /api/v3/orderList (Signed)
        Identifiers: orderListId OR listClientOrderId
        """
        if self.product != "spot":
            raise BinanceAPIError("Order lists are only available for Spot trading")
        params = [("symbol", symbol.upper())]
        if order_list_id:
            params.append(("orderListId", str(order_list_id)))
        if list_client_order_id:
            params.append(("listClientOrderId", str(list_client_order_id)))
        return self.request("DELETE", "/api/v3/orderList", params=params, signed=True, is_write=True)

    def get_open_order_lists(self):
        """Query all open Spot order lists."""
        if self.product != "spot":
            raise BinanceAPIError("Order lists are only available for Spot trading")
        return self.request("GET", "/api/v3/openOrderList", signed=True)

    # =========================================================================
    # FUTURES ALGO SERVICE (CONDITIONAL EXITS)
    # =========================================================================

    def create_futures_algo_order(self, symbol, side, algo_type, trigger_price,
                                  quantity=None, client_algo_id=None, reduce_only=True,
                                  position_side="BOTH", **extra):
        """Submit a conditional order on USD(S)-M Futures via Algo Service.

        Endpoint:        POST /fapi/v1/algoOrder (Signed)
        Required:        symbol, side (BUY/SELL), algoType, triggerPrice
        Algo Types:      STOP_MARKET, TAKE_PROFIT_MARKET, TRAILING_STOP_MARKET
        Identifiers:     clientAlgoId (<=36 chars) -> returns algoId
        Query Endpoint:  GET /fapi/v1/algoOrder (by algoId or clientAlgoId)
        Open Endpoint:   GET /fapi/v1/openAlgoOrders
        Cancel Endpoint: DELETE /fapi/v1/algoOrder (by algoId or clientAlgoId)
        """
        if self.product != "futures":
            raise BinanceAPIError("Algo orders are only available for Futures trading")

        params = [
            ("symbol", symbol.upper()),
            ("side", side.upper()),
            ("algoType", algo_type.upper()),
            ("triggerPrice", str(trigger_price)),
            ("positionSide", str(position_side).upper()),
        ]
        if str(position_side).upper() == "BOTH" and reduce_only is not None:
            params.append(("reduceOnly", "true" if reduce_only else "false"))
        if quantity is not None:
            params.append(("quantity", str(quantity)))
        if client_algo_id:
            params.append(("clientAlgoId", str(client_algo_id)))

        for k, v in extra.items():
            if v is not None:
                params.append((str(k), str(v)))

        return self.request("POST", "/fapi/v1/algoOrder", params=params, signed=True, is_write=True)

    def get_futures_algo_order(self, algo_id=None, client_algo_id=None):
        """Query status of a Futures conditional algo order."""
        if self.product != "futures":
            raise BinanceAPIError("Algo orders are only available for Futures trading")
        params = []
        if algo_id:
            params.append(("algoId", str(algo_id)))
        if client_algo_id:
            params.append(("clientAlgoId", str(client_algo_id)))
        return self.request("GET", "/fapi/v1/algoOrder", params=params, signed=True)

    def get_open_futures_algo_orders(self, symbol=None):
        """Query open Futures algo orders."""
        if self.product != "futures":
            raise BinanceAPIError("Algo orders are only available for Futures trading")
        params = [("symbol", symbol.upper())] if symbol else None
        return self.request("GET", "/fapi/v1/openAlgoOrders", params=params, signed=True)

    def cancel_futures_algo_order(self, algo_id=None, client_algo_id=None):
        """Cancel an open Futures conditional algo order."""
        if self.product != "futures":
            raise BinanceAPIError("Algo orders are only available for Futures trading")
        params = []
        if algo_id:
            params.append(("algoId", str(algo_id)))
        if client_algo_id:
            params.append(("clientAlgoId", str(client_algo_id)))
        return self.request("DELETE", "/fapi/v1/algoOrder", params=params, signed=True, is_write=True)


# =============================================================================
# STRICT DECIMAL EXCHANGE FILTER PARSING & QUANTIZATION
# =============================================================================

def parse_exchange_filters(symbol_info):
    """Extract tickSize, stepSize, minQty, maxQty, minPrice, maxPrice, and notional requirements as Decimals."""
    filters = {
        "tick_size": Decimal("0.00000001"),
        "min_price": Decimal("0"),
        "max_price": Decimal("999999999"),
        "step_size": Decimal("0.00000001"),
        "min_qty": Decimal("0"),
        "max_qty": Decimal("999999999"),
        "min_notional": Decimal("5.0"),
        "base_asset": symbol_info.get("baseAsset"),
        "quote_asset": symbol_info.get("quoteAsset"),
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


def quantize_price(price, tick_size, rounding=ROUND_DOWN, min_price=None):
    """Quantize price as a mathematically valid multiple of tickSize using Decimal.

    Formula: quantized_value = (raw_value // tick_size) * tick_size (for ROUND_DOWN)
    Does NOT assume minPrice defines the origin of the valid tick grid.
    Supports directional rounding: ROUND_DOWN (default) or ROUND_UP.
    """
    if price is None:
        return None
    if isinstance(rounding, (Decimal, int, float)) and min_price is None:
        min_price = rounding
        rounding = ROUND_DOWN

    p = Decimal(str(price))
    ts = Decimal(str(tick_size)) if tick_size is not None else Decimal("0")
    if ts <= Decimal("0"):
        return p
    if p < Decimal("0"):
        return Decimal("0")
    steps = p // ts
    remainder = p % ts
    if rounding in (ROUND_UP, "UP", "CEILING") and remainder > Decimal("0"):
        steps += Decimal("1")
    return steps * ts


def quantize_quantity(qty, step_size, rounding=ROUND_DOWN, min_qty=None):
    """Quantize quantity as a mathematically valid multiple of stepSize using Decimal.

    Formula: quantized_value = (raw_value // step_size) * step_size (for ROUND_DOWN)
    Does NOT assume minQty defines the origin of the step grid.
    Supports directional rounding: ROUND_DOWN (default) or ROUND_UP.
    """
    if qty is None:
        return None
    if isinstance(rounding, (Decimal, int, float)) and min_qty is None:
        min_qty = rounding
        rounding = ROUND_DOWN

    q = Decimal(str(qty))
    ss = Decimal(str(step_size)) if step_size is not None else Decimal("0")
    if ss <= Decimal("0"):
        return q
    if q < Decimal("0"):
        return Decimal("0")
    steps = q // ss
    remainder = q % ss
    if rounding in (ROUND_UP, "UP", "CEILING") and remainder > Decimal("0"):
        steps += Decimal("1")
    return steps * ss


def validate_order(symbol, side, price, quantity, filters, round_price=ROUND_DOWN, round_qty=ROUND_DOWN):
    """Validate order parameters against Binance filters using Decimal exclusively."""
    p = Decimal(str(price)) if price is not None else None
    q = Decimal(str(quantity))
    errors = []

    ss = filters.get("step_size", Decimal("0.00000001"))
    min_q = filters.get("min_qty", Decimal("0"))
    max_q = filters.get("max_qty", Decimal("999999999"))

    q_quant = quantize_quantity(q, ss, rounding=round_qty)
    if min_q > Decimal("0") and (q < min_q or q_quant < min_q):
        errors.append("Quantity %s is below minimum LOT_SIZE %s" % (q, min_q))
    if max_q > Decimal("0") and q_quant > max_q:
        errors.append("Quantity %s exceeds maximum LOT_SIZE %s" % (q_quant, max_q))

    p_quant = None
    notional = None
    if p is not None:
        ts = filters.get("tick_size", Decimal("0.00000001"))
        min_p = filters.get("min_price", Decimal("0"))
        max_p = filters.get("max_price", Decimal("999999999"))
        min_notional = filters.get("min_notional", Decimal("5.0"))

        p_quant = quantize_price(p, ts, rounding=round_price)
        if min_p > Decimal("0") and p_quant < min_p:
            errors.append("Price %s is below PRICE_FILTER minPrice %s" % (p_quant, min_p))
        if max_p > Decimal("0") and p_quant > max_p:
            errors.append("Price %s exceeds PRICE_FILTER maxPrice %s" % (p_quant, max_p))
        notional = p_quant * q_quant
        if min_notional > Decimal("0") and notional < min_notional:
            errors.append("Order notional $%s is below MIN_NOTIONAL $%s" % (notional, min_notional))

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


def detect_gaps(sorted_klines, interval_ms):
    """Check strict timestamp continuity (t[i] - t[i-1] == interval_ms).

    Never fabricate or interpolate missing candles. Return all gap segments.
    """
    gaps = []
    for i in range(1, len(sorted_klines)):
        expected = int(sorted_klines[i - 1][0]) + interval_ms
        actual = int(sorted_klines[i][0])
        if actual > expected:
            missing_count = (actual - expected) // interval_ms
            gaps.append({
                "gap_index": i,
                "expected": expected,
                "actual": actual,
                "missing_candles": missing_count,
                "missing_duration_ms": actual - expected
            })
    return gaps


def split_windows_at_gaps(sorted_klines, interval_ms):
    """Split sorted klines into continuous window segments where no gap exists.

    This ensures simulation runs never bridge across missing data segments.
    """
    if not sorted_klines:
        return []
    windows = []
    current_window = [sorted_klines[0]]
    for i in range(1, len(sorted_klines)):
        expected = int(sorted_klines[i - 1][0]) + interval_ms
        actual = int(sorted_klines[i][0])
        if actual == expected:
            current_window.append(sorted_klines[i])
        else:
            windows.append(current_window)
            current_window = [sorted_klines[i]]
    if current_window:
        windows.append(current_window)
    return windows

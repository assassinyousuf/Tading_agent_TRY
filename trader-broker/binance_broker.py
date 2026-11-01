#!/usr/bin/env python3
"""Binance Execution Broker for Spot and USD(S)-M Futures.

Pure Python 3.8+ standard library. Zero external pip dependencies.
Implements:
- Production safety gates: Default --dry-run mode, testnet default ("paper": true),
  and explicit --confirm-live required for real-money execution.
- Pre-submission client order ID persistence in .state/orders_journal.json.
- Separated lifecycles and status reconciliation:
  * Spot Market Entry -> net-of-fee executedQty -> Modern POST /api/v3/orderList/oco
  * Spot Pending Limit Entry -> Atomic POST /api/v3/orderList/otoco
  * USD(S)-M Futures Entry -> GET /fapi/v1/positionSide/dual -> Futures Algo Service (/fapi/v1/algoOrder)
- Ambiguous Submission Recovery (HTTP 5xx / timeout state reconciliation before retry).
- Bounded Emergency Recovery: Circuit breaker lock, open exposure verification,
  orphaned leg cleanup, and strictly bounded closing orders.
"""

import argparse
import datetime
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import logging
import os
import sys
import time
import uuid

# Path setup
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from binance_client import (
    BinanceClient,
    BinanceAPIError,
    BinanceAmbiguousError,
    parse_exchange_filters,
    quantize_price,
    quantize_quantity,
    validate_order,
    SPOT_LIVE_HOST,
    SPOT_TESTNET_HOST,
    FUTURES_LIVE_HOST,
    FUTURES_TESTNET_HOST,
)

logger = logging.getLogger("binance_broker")

JOURNAL_PATH = ".state/orders_journal.json"
CONFIG_PATH = "trader/config.json"
ORDERS_PATH = "trader/orders.json"
RISK_STATE_PATH = "trader/state/risk.json"
BROKER_STATE_PATH = "trader/state/broker.json"
PORTFOLIO_PATH = "trader/portfolio.json"


# =============================================================================
# STATE & JOURNAL PERSISTENCE
# =============================================================================

def load_json(path, default=None):
    if not path:
        return default if default is not None else {}
    if not os.path.exists(path):
        bak = path + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        logger.error("Failed to load %s: %s (attempting backup)", path, e)
        bak = path + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as eb:
                logger.error("Backup %s also failed: %s", bak, eb)
        return default if default is not None else {}


def write_json(path, data):
    """Crash-consistent atomic JSON writer with fsync and automatic backup."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp_path = "%s.tmp.%s.%d" % (path, uuid.uuid4().hex[:6], int(time.time() * 1000) % 100000)
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.exists(path):
            bak_path = path + ".bak"
            try:
                # Copy to backup before replacing
                with open(path, "r", encoding="utf-8") as src, open(bak_path, "w", encoding="utf-8") as dst:
                    dst.write(src.read())
            except Exception:
                pass
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def load_journal(path=JOURNAL_PATH):
    return load_json(path, {"circuit_breaker": {"locked": False, "reason": None}, "entries": []})


def save_journal(journal, path=JOURNAL_PATH):
    write_json(path, journal)


def persist_order_intent(symbol, side, qty, order_type, price=None, stop=None, target=None,
                         market="spot", path=JOURNAL_PATH):
    """Generate unique client order IDs and persist intent before transmitting."""
    journal = load_journal(path)
    ts = int(time.time() * 1000)
    uid = uuid.uuid4().hex[:8]

    cid_entry = "trader_e_%s_%d" % (uid, ts % 1000000)
    cid_exit_list = "trader_l_%s_%d" % (uid, ts % 1000000)
    cid_sl = "trader_s_%s_%d" % (uid, ts % 1000000)
    cid_tp = "trader_t_%s_%d" % (uid, ts % 1000000)

    entry_rec = {
        "id": cid_entry,
        "symbol": symbol.upper(),
        "side": side.upper(),
        "qty": str(qty),
        "order_type": order_type.upper(),
        "price": str(price) if price is not None else None,
        "stop": str(stop) if stop is not None else None,
        "target": str(target) if target is not None else None,
        "market": market.lower(),
        "list_client_order_id": cid_exit_list,
        "client_sl_id": cid_sl,
        "client_tp_id": cid_tp,
        "status": "INTENT",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "history": [{"status": "INTENT", "timestamp": time.time()}],
        "broker_order_id": None,
        "fill_info": {}
    }
    journal.setdefault("entries", []).append(entry_rec)
    save_journal(journal, path)
    return entry_rec


def update_journal_entry(cid, status, fill_info=None, broker_order_id=None, path=JOURNAL_PATH):
    journal = load_journal(path)
    for e in journal.get("entries", []):
        if e.get("id") == cid or e.get("list_client_order_id") == cid:
            e["status"] = status
            if broker_order_id:
                e["broker_order_id"] = str(broker_order_id)
            if fill_info:
                e.setdefault("fill_info", {}).update(fill_info)
            e.setdefault("history", []).append({"status": status, "timestamp": time.time()})
            break
    save_journal(journal, path)


def set_circuit_breaker(locked, reason=None, path=JOURNAL_PATH):
    journal = load_journal(path)
    journal["circuit_breaker"] = {
        "locked": bool(locked),
        "reason": reason,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    save_journal(journal, path)


# =============================================================================
# AMBIGUOUS SUBMISSION RECOVERY & RECONCILIATION
# =============================================================================

def reconcile_ambiguous_order(client, symbol, client_order_id, is_algo=False, is_order_list=False):
    """Query Binance status endpoint to confirm whether an ambiguous order landed."""
    logger.info("Reconciling ambiguous order for %s (Client ID: %s)...", symbol, client_order_id)
    try:
        if is_algo:
            res = client.get_futures_algo_order(client_algo_id=client_order_id)
            if res and res.get("clientAlgoId") == client_order_id:
                return {"landed": True, "data": res, "status": res.get("algoStatus")}
        elif is_order_list:
            res = client.get_spot_order_list(orig_client_order_id=client_order_id)
            if res and res.get("listClientOrderId") == client_order_id:
                return {"landed": True, "data": res, "status": res.get("listOrderStatus")}
        else:
            res = client.get_order(symbol, orig_client_order_id=client_order_id)
            if res and res.get("clientOrderId") == client_order_id:
                return {"landed": True, "data": res, "status": res.get("status")}
    except BinanceAPIError as e:
        # Code -2011, -2013, or -1128: Unknown order / Order does not exist / Order list does not exist
        if e.code in (-2011, -2013, -1128) or "does not exist" in str(e).lower():
            return {"landed": False, "data": None, "status": "NOT_FOUND"}
        raise
    return {"landed": False, "data": None, "status": "UNKNOWN"}


# =============================================================================
# SPOT FILL RECONCILIATION & AVAILABLE QUANTITY VERIFICATION
# =============================================================================

def reconcile_spot_fill_and_available_qty(client, symbol, order_id, entry_res=None,
                                          filters=None, stop_price=None, target_price=None):
    """Determine genuine available base-asset quantity and actual fill commissions for Spot.

    Safety Principles:
    1. Zero assumptions: removes any hardcoded 0.10% fee assumption.
    2. Reads actual fill-level `commission` and `commissionAsset` data across all partial fills.
    3. Handles base-asset (e.g. SOL), quote-asset (e.g. USDT), BNB, or other fee assets correctly.
    4. Confirms actual free balance via client.get_account() to prevent trying to sell more than available.
    5. Validates quantized available quantity against stepSize, minQty, maxQty, and minNotional.
    6. If fee data or available quantity cannot be safely established, halts workflow without guessing.
    """
    filters = filters or {}
    symbol = symbol.upper()
    base_asset = filters.get("base_asset")
    quote_asset = filters.get("quote_asset")
    if not base_asset or not quote_asset:
        for q in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "BTC", "ETH", "BNB"):
            if symbol.endswith(q):
                base_asset = base_asset or symbol[:-len(q)]
                quote_asset = quote_asset or q
                break
        base_asset = (base_asset or symbol).upper()
        quote_asset = (quote_asset or "USDT").upper()

    # 1. Collect Fills
    fills = []
    if entry_res and isinstance(entry_res, dict) and entry_res.get("fills"):
        fills = entry_res.get("fills")
    elif client:
        try:
            trades = client.get_my_trades(symbol, order_id=order_id)
            if isinstance(trades, list) and len(trades) > 0:
                fills = trades
        except Exception as e:
            logger.error("Failed to fetch myTrades for order %s: %s", order_id, e)
            return {
                "valid": False,
                "reason": "MISSING_FEE_DATA",
                "error": "Could not retrieve fill-level trade records or fee data from exchange: %s" % e,
                "details": {"symbol": symbol, "order_id": order_id}
            }

    if not fills:
        return {
            "valid": False,
            "reason": "NO_FILLS_FOUND",
            "error": "No fill records returned by exchange for order %s" % order_id,
            "details": {"symbol": symbol, "order_id": order_id}
        }

    # 2. Process Fills & Fees
    total_exec_qty = Decimal("0")
    base_commission_deducted = Decimal("0")
    fees_summary = {}

    for i, f in enumerate(fills):
        if not isinstance(f, dict):
            return {
                "valid": False,
                "reason": "MALFORMED_FILL_DATA",
                "error": "Fill item %d is not a valid dictionary: %s" % (i, f),
                "details": {"fill_index": i}
            }
        qty_val = f.get("qty")
        comm_val = f.get("commission")
        comm_asset = f.get("commissionAsset")

        if qty_val is None or comm_val is None or not comm_asset:
            return {
                "valid": False,
                "reason": "MISSING_FEE_FIELD",
                "error": "Fill item %d missing required qty, commission, or commissionAsset" % i,
                "details": {"fill_index": i, "fill_data": f}
            }
        try:
            f_qty = Decimal(str(qty_val))
            f_comm = Decimal(str(comm_val))
        except Exception as e:
            return {
                "valid": False,
                "reason": "INVALID_FEE_NUMBER",
                "error": "Fill item %d contains invalid numeric values: %s" % (i, e),
                "details": {"fill_index": i, "fill_data": f}
            }

        total_exec_qty += f_qty
        asset_key = str(comm_asset).upper()
        fees_summary[asset_key] = fees_summary.get(asset_key, Decimal("0")) + f_comm

        if asset_key == base_asset.upper():
            base_commission_deducted += f_comm

    net_received_qty = total_exec_qty - base_commission_deducted
    if net_received_qty <= Decimal("0"):
        return {
            "valid": False,
            "reason": "ZERO_OR_NEGATIVE_NET_QTY",
            "error": "Net received quantity is <= 0 after deducting base fee of %s %s" % (base_commission_deducted, base_asset),
            "details": {"total_exec_qty": str(total_exec_qty), "base_commission_deducted": str(base_commission_deducted)}
        }

    # 3. Account Free Balance Verification
    free_balance = None
    if client:
        try:
            account = client.get_account()
            balances = account.get("balances", [])
            asset_bal = next((b for b in balances if b.get("asset") == base_asset.upper()), None)
            if asset_bal is None:
                return {
                    "valid": False,
                    "reason": "ASSET_NOT_IN_ACCOUNT",
                    "error": "Base asset %s not found in account balances" % base_asset,
                    "details": {"base_asset": base_asset}
                }
            free_balance = Decimal(str(asset_bal.get("free", "0")))
        except Exception as e:
            return {
                "valid": False,
                "reason": "ACCOUNT_RECONCILIATION_FAILED",
                "error": "Failed to query account balances for %s: %s" % (base_asset, e),
                "details": {"base_asset": base_asset}
            }

    # Available quantity cannot exceed verified free balance in account
    available_qty = net_received_qty
    if free_balance is not None:
        available_qty = min(net_received_qty, free_balance)

    # 4. Exchange Filter Validation
    step_size = filters.get("step_size", Decimal("0.00000001"))
    min_qty = filters.get("min_qty", Decimal("0"))
    max_qty = filters.get("max_qty", Decimal("999999999"))
    min_notional = filters.get("min_notional", Decimal("5.0"))

    quantized_sellable = quantize_quantity(available_qty, step_size, ROUND_DOWN)

    if quantized_sellable < min_qty:
        return {
            "valid": False,
            "reason": "QTY_FILTER_BELOW_MIN",
            "error": "Quantized sellable quantity %s is below exchange minQty %s" % (quantized_sellable, min_qty),
            "details": {"quantized_qty": str(quantized_sellable), "min_qty": str(min_qty)}
        }
    if quantized_sellable > max_qty:
        return {
            "valid": False,
            "reason": "QTY_FILTER_ABOVE_MAX",
            "error": "Quantized sellable quantity %s exceeds exchange maxQty %s" % (quantized_sellable, max_qty),
            "details": {"quantized_qty": str(quantized_sellable), "max_qty": str(max_qty)}
        }

    if stop_price is not None:
        stop_notional = quantized_sellable * Decimal(str(stop_price))
        if stop_notional < min_notional:
            return {
                "valid": False,
                "reason": "NOTIONAL_FILTER_STOP_FAILED",
                "error": "Exit notional at stop price (%s) is below minNotional %s" % (stop_notional, min_notional),
                "details": {"stop_notional": str(stop_notional), "min_notional": str(min_notional)}
            }

    if target_price is not None:
        target_notional = quantized_sellable * Decimal(str(target_price))
        if target_notional < min_notional:
            return {
                "valid": False,
                "reason": "NOTIONAL_FILTER_TARGET_FAILED",
                "error": "Exit notional at target price (%s) is below minNotional %s" % (target_notional, min_notional),
                "details": {"target_notional": str(target_notional), "min_notional": str(min_notional)}
            }

    return {
        "valid": True,
        "sellable_qty": quantized_sellable,
        "total_exec_qty": total_exec_qty,
        "net_received_qty": net_received_qty,
        "free_balance": free_balance,
        "base_commission_deducted": base_commission_deducted,
        "fees_summary": {k: str(v) for k, v in fees_summary.items()},
        "fills_count": len(fills)
    }


# =============================================================================
# ACTIVE ORDER LIFECYCLE RECONCILIATION (CRASH CONSISTENT & IDEMPOTENT)
# =============================================================================

def reconcile_active_orders(client, symbol=None, journal_path=JOURNAL_PATH):
    """Reconcile active orders in the journal with the exchange state.

    Handles process restarts safely without duplicate order placement:
    1. Inspects all active entries (INTENT, AMBIGUOUS, ENTRY_PLACED, PLACED_OTOCO, OCO_ACTIVE, etc.)
    2. Synchronizes status with exchange queries (GET /api/v3/order or GET /api/v3/orderList).
    3. Detects fills and exit activations:
       - If working order filled -> updates to EXITS_ACTIVE / OCO_ACTIVE.
       - If an exit leg filled (target or stop) -> transitions to CLOSED_TARGET or CLOSED_STOP.
       - Verifies that the other exit leg was canceled (the OCO guarantee).
    4. Detects external or manual cancellations -> updates to CLOSED_CANCELED.
    5. Saves updated journal atomically.
    """
    journal = load_journal(journal_path)
    entries = journal.get("entries", [])
    updated = False
    report = {"checked": 0, "updated": 0, "active_count": 0, "results": []}

    ACTIVE_STATUSES = {
        "INTENT", "AMBIGUOUS", "ENTRY_PLACED", "PLACED_OTOCO",
        "OCO_ACTIVE", "FUTURES_ENTRY_PLACED", "FUTURES_BRACKET_ACTIVE"
    }

    for e in entries:
        status = e.get("status")
        if status not in ACTIVE_STATUSES:
            continue

        sym = e.get("symbol")
        if symbol and sym != symbol.upper():
            continue

        report["checked"] += 1
        cid = e.get("id")
        list_cid = e.get("list_client_order_id")
        broker_order_id = e.get("broker_order_id")
        market = e.get("market", "spot").lower()
        res_info = {"id": cid, "symbol": sym, "prev_status": status}

        try:
            if market == "spot":
                # Check Order List (OTOCO or OCO)
                if status in ("PLACED_OTOCO", "OCO_ACTIVE"):
                    ol = None
                    try:
                        if broker_order_id:
                            ol = client.get_spot_order_list(order_list_id=broker_order_id)
                        elif list_cid:
                            ol = client.get_spot_order_list(orig_client_order_id=list_cid)
                    except Exception as ex_ol:
                        logger.warning("Could not query order list for %s: %s", cid, ex_ol)

                    if ol:
                        list_status = ol.get("listOrderStatus")
                        reports = ol.get("orderReports", [])
                        e.setdefault("fill_info", {}).update(ol)

                        if list_status == "EXECUTING":
                            e["status"] = "OCO_ACTIVE"
                            res_info["new_status"] = "OCO_ACTIVE"
                            res_info["listOrderStatus"] = list_status
                            updated = True
                        elif list_status == "ALL_DONE":
                            target_filled = False
                            stop_filled = False
                            all_canceled = True
                            for rep in reports:
                                o_stat = rep.get("status")
                                o_type = rep.get("type")
                                if o_stat == "FILLED":
                                    all_canceled = False
                                    if o_type in ("LIMIT_MAKER", "LIMIT"):
                                        target_filled = True
                                    elif "STOP" in o_type:
                                        stop_filled = True
                                elif o_stat != "CANCELED":
                                    all_canceled = False

                            if target_filled:
                                e["status"] = "CLOSED_TARGET"
                                res_info["new_status"] = "CLOSED_TARGET"
                            elif stop_filled:
                                e["status"] = "CLOSED_STOP"
                                res_info["new_status"] = "CLOSED_STOP"
                            elif all_canceled:
                                e["status"] = "CLOSED_CANCELED"
                                res_info["new_status"] = "CLOSED_CANCELED"
                            else:
                                e["status"] = "CLOSED_ALL_DONE"
                                res_info["new_status"] = "CLOSED_ALL_DONE"
                            updated = True
                            e.setdefault("history", []).append({
                                "status": e["status"], "timestamp": time.time(), "reconciled": True
                            })
                        elif list_status in ("REJECT", "CANCEL"):
                            e["status"] = "CLOSED_CANCELED"
                            res_info["new_status"] = "CLOSED_CANCELED"
                            updated = True
                            e.setdefault("history", []).append({
                                "status": e["status"], "timestamp": time.time(), "reconciled": True
                            })

                elif status in ("ENTRY_PLACED", "AMBIGUOUS", "INTENT"):
                    ord_res = None
                    try:
                        ord_res = client.get_order(sym, orig_client_order_id=cid)
                    except Exception as ex_o:
                        logger.warning("Could not query order for %s: %s", cid, ex_o)

                    if ord_res:
                        o_status = ord_res.get("status")
                        e.setdefault("fill_info", {}).update(ord_res)
                        if o_status == "FILLED":
                            e["status"] = "ENTRY_FILLED"
                            res_info["new_status"] = "ENTRY_FILLED"
                            updated = True
                        elif o_status in ("CANCELED", "REJECTED", "EXPIRED"):
                            e["status"] = "CLOSED_" + o_status
                            res_info["new_status"] = e["status"]
                            updated = True
                        e.setdefault("history", []).append({
                            "status": e["status"], "timestamp": time.time(), "reconciled": True
                        })
            else:
                ord_res = None
                try:
                    ord_res = client.get_order(sym, orig_client_order_id=cid)
                except Exception:
                    pass
                if ord_res:
                    o_status = ord_res.get("status")
                    if o_status in ("CANCELED", "REJECTED", "EXPIRED"):
                        e["status"] = "CLOSED_" + o_status
                        res_info["new_status"] = e["status"]
                        updated = True

        except Exception as err:
            logger.error("Error reconciling entry %s: %s", cid, err)
            res_info["error"] = str(err)

        if e.get("status") in ACTIVE_STATUSES:
            report["active_count"] += 1
        report["results"].append(res_info)

    if updated:
        save_journal(journal, journal_path)
        report["updated"] = sum(1 for r in report["results"] if r.get("new_status"))

    return report


# =============================================================================
# BOUNDED EMERGENCY RECOVERY PROCEDURE
# =============================================================================

def bounded_emergency_recovery(client, symbol, market="spot", journal_path=JOURNAL_PATH):
    """Execute the strict 5-step bounded emergency recovery procedure.

    Never blindly flatten or loop market orders!
    Step 1: Set circuit breaker lock halting all new entries.
    Step 2: Reconcile open orders, order lists, and algo orders.
    Step 3: Query verified live exposure (Spot asset balances or Futures positionAmt).
    Step 4: Cancel orphaned exit legs so they cannot double-execute, then submit
            a bounded closing order sized strictly to verified open exposure.
    Step 5: Re-query account / position state to verify exposure is reduced to zero.
    """
    logger.critical("EMERGENCY RECOVERY INITIATED FOR %s (%s)", symbol, market.upper())
    report = {"symbol": symbol, "market": market, "actions": [], "recovered": False}

    # Step 1: Set circuit breaker lock
    set_circuit_breaker(True, "Emergency recovery triggered for %s" % symbol, path=journal_path)
    report["actions"].append("Circuit breaker lock engaged: All new entries halted.")

    # Step 2 & 4: Cancel all existing open orders / orphaned exit legs first
    try:
        if market == "futures":
            client.cancel_all_open_orders(symbol)
            report["actions"].append("Canceled standard open futures orders.")
            try:
                open_algos = client.get_open_futures_algo_orders(symbol)
                for a in open_algos:
                    aid = a.get("algoId")
                    if aid:
                        client.cancel_futures_algo_order(algo_id=aid)
                report["actions"].append("Canceled orphaned futures algo orders.")
            except Exception as ea:
                report["actions"].append("Note on algo cancel: %s" % ea)
        else:
            client.cancel_all_open_orders(symbol)
            report["actions"].append("Canceled open spot orders.")
    except Exception as e:
        report["actions"].append("Warning canceling open orders: %s" % e)

    # Step 3: Query actual live exposure
    open_exposure = Decimal("0")
    position_side = "BOTH"
    close_side = "SELL"

    if market == "futures":
        pos_risk = client.get_position_risk(symbol)
        for p in pos_risk:
            amt = Decimal(str(p.get("positionAmt", "0")))
            if abs(amt) > Decimal("0"):
                open_exposure = abs(amt)
                close_side = "SELL" if amt > Decimal("0") else "BUY"
                position_side = p.get("positionSide", "BOTH")
                break
    else:
        # Spot: check base asset free balance
        base_asset = symbol[:-4] if symbol.endswith("USDT") else symbol
        acct = client.get_account()
        for b in acct.get("balances", []):
            if b.get("asset") == base_asset:
                open_exposure = Decimal(str(b.get("free", "0")))
                close_side = "SELL"
                break

    report["open_exposure"] = str(open_exposure)
    report["close_side"] = close_side

    # Step 4: Submit strictly bounded closing order if exposure exists
    if open_exposure > Decimal("0"):
        # Validate/quantize against symbol filters
        info = client.get_exchange_info(symbol)
        sym_info = next((s for s in info.get("symbols", []) if s.get("symbol") == symbol), {})
        filters = parse_exchange_filters(sym_info)
        qty_to_close = quantize_quantity(open_exposure, filters["step_size"], filters["min_qty"])

        if qty_to_close >= filters["min_qty"]:
            close_cid = "trader_close_%s_%d" % (uuid.uuid4().hex[:8], int(time.time() * 1000) % 1000000)
            report["actions"].append("Submitting bounded closing order: %s %s %s" % (close_side, qty_to_close, symbol))
            try:
                if market == "futures":
                    client.create_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="MARKET",
                        quantity=qty_to_close,
                        new_client_order_id=close_cid,
                        reduce_only=True if position_side == "BOTH" else False,
                        position_side=position_side
                    )
                else:
                    client.create_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="MARKET",
                        quantity=qty_to_close,
                        new_client_order_id=close_cid
                    )
                report["actions"].append("Closing order submitted successfully.")
            except Exception as ec:
                report["actions"].append("CRITICAL: Failed to submit closing order: %s" % ec)
        else:
            report["actions"].append("Open exposure %s is below min_qty %s; cannot close via market order."
                                    % (open_exposure, filters["min_qty"]))

    # Step 5: Re-query account / position state to verify exposure is reduced to zero
    final_exposure = Decimal("0")
    if market == "futures":
        pos_risk2 = client.get_position_risk(symbol)
        for p in pos_risk2:
            amt = Decimal(str(p.get("positionAmt", "0")))
            if abs(amt) > Decimal("0"):
                final_exposure = abs(amt)
                break
    else:
        base_asset = symbol[:-4] if symbol.endswith("USDT") else symbol
        acct2 = client.get_account()
        for b in acct2.get("balances", []):
            if b.get("asset") == base_asset:
                final_exposure = Decimal(str(b.get("free", "0")))
                break

    report["final_exposure"] = str(final_exposure)
    report["recovered"] = (final_exposure == Decimal("0") or final_exposure < Decimal("0.0001"))
    if not report["recovered"]:
        report["actions"].append("CRITICAL ALERT: Exposure remains at %s! Exiting cleanly without blind looping." % final_exposure)
    else:
        report["actions"].append("VERIFIED: Net exposure successfully reduced to zero.")

    return report


# =============================================================================
# UNIFIED BRACKET ORDER EXECUTION ENGINE
# =============================================================================

def execute_bracket_order(symbol, side, qty, entry=None, stop=None, target=None,
                          order_type="MARKET", market="spot", dry_run=True,
                          confirm_live=False, cfg=None, journal_path=JOURNAL_PATH):
    """Execute or simulate a Binance bracket order with strict safety defaults."""
    cfg = cfg or load_json(CONFIG_PATH, {})
    paper = cfg.get("paper", True)
    symbol = symbol.upper()
    side = side.upper()
    market = market.lower()
    order_type = order_type.upper()

    print("=" * 70)
    print("BINANCE EXECUTION DESK")
    print("=" * 70)
    print("Symbol / Market : %s (%s)" % (symbol, market.upper()))
    print("Mode            : %s" % ("DRY RUN (Simulation Only)" if dry_run else ("TESTNET" if paper else "LIVE REAL MONEY")))
    print("Order Intent    : %s %s %s | Entry: %s | Stop: %s | Target: %s"
          % (side, qty, symbol, entry or "MARKET", stop, target))

    # Safety Gate 1: Live Confirmation
    if not dry_run and not paper and not confirm_live:
        print("\n[SAFETY GATE REJECTION]")
        print("Live real-money trading requires explicit confirmation!")
        print("To place real orders on Binance Live Production, pass --confirm-live.")
        return 1

    # Safety Gate 2: Circuit Breaker
    journal = load_journal(journal_path)
    cb = journal.get("circuit_breaker", {})
    if cb.get("locked"):
        print("\n[CIRCUIT BREAKER ENGAGED]")
        print("Trading halted: %s (at %s)" % (cb.get("reason"), cb.get("timestamp")))
        print("Reset circuit breaker in %s before placing new orders." % journal_path)
        return 1

    # Safety Gate 3: Spot Short Position Rejection
    if market == "spot" and side != "BUY":
        print("\n[SPOT MARKET SHORT REJECTION]")
        print("Binance Spot does not support short positions. Only BUY entries are permitted for Spot bracket orders.")
        return 1

    # Filter Validation with Decimal
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_SECRET_KEY", "")
    client = BinanceClient(api_key=api_key, api_secret=api_secret, product=market, testnet=paper)

    try:
        ex_info = client.get_exchange_info(symbol)
        sym_info = next((s for s in ex_info.get("symbols", []) if s.get("symbol") == symbol), {})
        filters = parse_exchange_filters(sym_info)
    except Exception as e:
        logger.warning("Could not fetch exchangeInfo (%s); using default filters", e)
        filters = {
            "tick_size": Decimal("0.01"), "min_price": Decimal("0.01"), "max_price": Decimal("1000000"),
            "step_size": Decimal("0.01"), "min_qty": Decimal("0.01"), "max_qty": Decimal("900000"),
            "min_notional": Decimal("5.0")
        }

    val_res = validate_order(symbol, side, price=entry or stop or Decimal("100"), quantity=qty, filters=filters)
    if not val_res["valid"]:
        print("\n[EXCHANGE FILTER VALIDATION FAILED]")
        for err in val_res["errors"]:
            print("  - %s" % err)
        return 1

    q_qty = val_res["quantized_qty"]
    q_entry = quantize_price(entry, filters["tick_size"]) if entry else None
    q_stop = quantize_price(stop, filters["tick_size"]) if stop else None
    q_target = quantize_price(target, filters["tick_size"]) if target else None

    print("-" * 70)
    print("Filter Validation: PASSED")
    print("  Quantized Qty  : %s (stepSize: %s)" % (q_qty, filters["step_size"]))
    if q_entry:
        print("  Quantized Entry: %s (tickSize: %s)" % (q_entry, filters["tick_size"]))
    if q_stop:
        print("  Quantized Stop : %s (tickSize: %s)" % (q_stop, filters["tick_size"]))
    if q_target:
        print("  Quantized Target: %s (tickSize: %s)" % (q_target, filters["tick_size"]))

    # DRY RUN: Print plan and exit
    if dry_run:
        print("\n[DRY RUN SUMMARY]")
        print("Validations passed. Signed payload constructed without transmitting network requests.")
        print("Execution lifecycle plan:")
        if market == "spot":
            if order_type == "MARKET":
                print("  1. Submit POST /api/v3/order (type=MARKET, newClientOrderId)")
                print("  2. Reconcile fill via GET /api/v3/order (deduct base fee)")
                print("  3. Submit protective exit POST /api/v3/orderList/oco (abovePrice=%s, belowStopPrice=%s)" % (q_target, q_stop))
            else:
                print("  1. Submit atomic POST /api/v3/orderList/otoco (workingPrice=%s, target=%s, stop=%s)" % (q_entry, q_target, q_stop))
        else:
            print("  1. Query position mode GET /fapi/v1/positionSide/dual")
            print("  2. Submit entry POST /fapi/v1/order")
            print("  3. Submit conditional SL/TP via Futures Algo Service POST /fapi/v1/algoOrder")
        print("=" * 70)
        return 0

    # Persist intent before network submission
    intent = persist_order_intent(symbol, side, q_qty, order_type, price=q_entry,
                                  stop=q_stop, target=q_target, market=market, path=journal_path)
    cid_entry = intent["id"]
    print("\nOrder Intent Logged: %s (Client Order ID: %s)" % (journal_path, cid_entry))

    # EXECUTE: Spot vs Futures
    if market == "spot":
        if order_type == "LIMIT":
            # Atomic Spot OTOCO
            print("\nSubmitting Atomic Spot OTOCO order...")
            try:
                otoco_res = client.create_spot_otoco(
                    symbol=symbol,
                    working_side=side,
                    working_price=q_entry,
                    working_qty=q_qty,
                    pending_side="SELL" if side == "BUY" else "BUY",
                    pending_qty=q_qty,
                    pending_above_price=q_target,
                    pending_below_stop_price=q_stop,
                    list_client_order_id=intent["list_client_order_id"],
                    working_client_order_id=cid_entry
                )
                print("  -> OTOCO Placed! Order List ID: %s" % otoco_res.get("orderListId"))
                update_journal_entry(cid_entry, "PLACED_OTOCO", fill_info=otoco_res,
                                     broker_order_id=otoco_res.get("orderListId"), path=journal_path)
                return 0
            except BinanceAmbiguousError as ae:
                print("  -> AMBIGUOUS TIMEOUT ON OTOCO: %s" % ae)
                update_journal_entry(cid_entry, "AMBIGUOUS", path=journal_path)
                rec = reconcile_ambiguous_order(client, symbol, intent["list_client_order_id"], is_order_list=True)
                if not rec["landed"]:
                    # Also check if working order landed
                    rec = reconcile_ambiguous_order(client, symbol, cid_entry)
                print("  Reconciliation result: %s" % rec)
                return 1
            except Exception as e:
                print("  -> FAILED to place OTOCO: %s" % e)
                update_journal_entry(cid_entry, "FAILED", path=journal_path)
                return 1
        else:
            # Spot Market Entry + Post-Fill OCO
            print("\nSubmitting Spot Market Entry order...")
            try:
                entry_res = client.create_order(
                    symbol=symbol,
                    side=side,
                    order_type="MARKET",
                    quantity=q_qty,
                    new_client_order_id=cid_entry,
                    newOrderRespType="FULL"
                )
            except BinanceAmbiguousError as ae:
                print("  -> AMBIGUOUS ENTRY SUBMISSION: %s" % ae)
                update_journal_entry(cid_entry, "AMBIGUOUS", path=journal_path)
                rec = reconcile_ambiguous_order(client, symbol, cid_entry)
                if not rec["landed"]:
                    print("  Order did not land on exchange. Halting.")
                    return 1
                entry_res = rec["data"]

            oid = entry_res.get("orderId")
            print("  -> Entry Placed! Order ID: %s" % oid)
            update_journal_entry(cid_entry, "ENTRY_PLACED", fill_info=entry_res,
                                 broker_order_id=oid, path=journal_path)

            # Reconcile fill quantity and actual net-of-fee calculation
            print("  Reconciling actual fill status and commissions...")
            recon = reconcile_spot_fill_and_available_qty(
                client=client,
                symbol=symbol,
                order_id=oid,
                entry_res=entry_res,
                filters=filters,
                stop_price=q_stop,
                target_price=q_target
            )
            if not recon["valid"]:
                print("\n" + "!" * 70)
                print("[CRITICAL ALERT: SPOT EXIT WORKFLOW HALTED]")
                print("Reason : %s" % recon["reason"])
                print("Error  : %s" % recon["error"])
                print("Details: %s" % json.dumps(recon.get("details", {})))
                print("Action : Halting exit workflow without guessing. Locking circuit breaker.")
                print("!" * 70)
                set_circuit_breaker(True, "Spot exit fee/reconciliation safety: %s" % recon["reason"], path=journal_path)
                update_journal_entry(cid_entry, "EXIT_HALTED_SAFETY", fill_info=recon, path=journal_path)
                return 1

            net_sellable_qty = recon["sellable_qty"]
            print("  -> Actual Fill Reconciliation Succeeded:")
            print("     Fills Processed : %d" % recon["fills_count"])
            print("     Total Executed  : %s" % recon["total_exec_qty"])
            print("     Commissions     : %s" % recon["fees_summary"])
            print("     Net Received    : %s" % recon["net_received_qty"])
            if recon["free_balance"] is not None:
                print("     Account Free    : %s" % recon["free_balance"])
            print("     Sellable Qty    : %s" % net_sellable_qty)

            # Submit protective OCO exit
            print("  Submitting Protective Exit OCO (Target: %s, Stop: %s, Net Qty: %s)..."
                  % (q_target, q_stop, net_sellable_qty))
            try:
                oco_res = client.create_spot_oco(
                    symbol=symbol,
                    side="SELL" if side == "BUY" else "BUY",
                    quantity=net_sellable_qty,
                    price=q_target,
                    stop_price=q_stop,
                    list_client_order_id=intent["list_client_order_id"],
                    limit_client_order_id=intent["client_tp_id"],
                    stop_client_order_id=intent["client_sl_id"]
                )
                print("  -> Protective OCO active! Order List ID: %s" % oco_res.get("orderListId"))
                update_journal_entry(cid_entry, "OCO_ACTIVE", fill_info=oco_res, path=journal_path)
                return 0
            except Exception as e_oco:
                print("  -> CRITICAL: Protective OCO failed after fill: %s" % e_oco)
                update_journal_entry(cid_entry, "OCO_FAILED", path=journal_path)
                recovery_res = bounded_emergency_recovery(client, symbol, market="spot", journal_path=journal_path)
                print("  Emergency Recovery result: %s" % recovery_res)
                return 1

    else:
        # Futures Execution Workflow
        print("\nQuerying Futures position side mode...")
        pos_mode = client.get_position_mode()
        hedge_mode = pos_mode.get("dualSidePosition", False)
        pos_side = ("LONG" if side == "BUY" else "SHORT") if hedge_mode else "BOTH"
        print("  Mode: %s (positionSide: %s)" % ("Hedge Mode" if hedge_mode else "One-Way Mode", pos_side))

        print("Submitting Futures Entry order...")
        try:
            f_entry = client.create_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=q_qty,
                price=q_entry if order_type == "LIMIT" else None,
                time_in_force="GTC" if order_type == "LIMIT" else None,
                new_client_order_id=cid_entry,
                position_side=pos_side,
                reduce_only=False
            )
            oid = f_entry.get("orderId")
            print("  -> Futures Entry Placed! Order ID: %s" % oid)
            update_journal_entry(cid_entry, "FUTURES_ENTRY_PLACED", fill_info=f_entry,
                                 broker_order_id=oid, path=journal_path)
        except BinanceAmbiguousError as ae:
            print("  -> AMBIGUOUS FUTURES ENTRY: %s" % ae)
            update_journal_entry(cid_entry, "AMBIGUOUS", path=journal_path)
            rec = reconcile_ambiguous_order(client, symbol, cid_entry)
            if not rec["landed"]:
                print("  Futures entry did not land. Halting.")
                return 1
            f_entry = rec["data"]

        # Conditional protective exits via Futures Algo Service
        exit_side = "SELL" if side == "BUY" else "BUY"
        print("Submitting Conditional Exits via Futures Algo Service...")
        try:
            sl_res = client.create_futures_algo_order(
                symbol=symbol,
                side=exit_side,
                algo_type="STOP_MARKET",
                trigger_price=q_stop,
                quantity=q_qty,
                client_algo_id=intent["client_sl_id"],
                reduce_only=False if hedge_mode else True,
                position_side=pos_side
            )
            print("  -> Stop Loss Algo Placed! Algo ID: %s" % sl_res.get("algoId"))

            tp_res = client.create_futures_algo_order(
                symbol=symbol,
                side=exit_side,
                algo_type="TAKE_PROFIT_MARKET",
                trigger_price=q_target,
                quantity=q_qty,
                client_algo_id=intent["client_tp_id"],
                reduce_only=False if hedge_mode else True,
                position_side=pos_side
            )
            print("  -> Take Profit Algo Placed! Algo ID: %s" % tp_res.get("algoId"))
            update_journal_entry(cid_entry, "FUTURES_BRACKET_ACTIVE",
                                 fill_info={"sl": sl_res, "tp": tp_res}, path=journal_path)
            return 0
        except Exception as e_algo:
            print("  -> CRITICAL: Conditional Algo Exits failed: %s" % e_algo)
            update_journal_entry(cid_entry, "ALGO_EXIT_FAILED", path=journal_path)
            recovery_res = bounded_emergency_recovery(client, symbol, market="futures", journal_path=journal_path)
            print("  Emergency Recovery result: %s" % recovery_res)
            return 1


# =============================================================================
# CLI SUBCOMMAND HANDLERS
# =============================================================================

def cmd_status(args, cfg):
    paper = cfg.get("paper", True)
    product = cfg.get("product", "spot").upper()
    client = BinanceClient(product=product.lower(), testnet=paper)

    print("=" * 60)
    print("BINANCE DESK STATUS")
    print("=" * 60)
    print("Environment: %s" % ("TESTNET (Paper Trading)" if paper else "LIVE TRADING (Real Capital)"))
    print("Product:     %s" % product)
    print("Host URL:    %s" % client.base_url)

    try:
        server_time = client.get_time()
        print("Ping/Time:   Server time %s (offset %d ms) [CONNECTED]" % (server_time, client.time_offset_ms))
    except Exception as e:
        print("Ping/Time:   FAILED to connect: %s" % e)
        return 1

    if not client.api_key or not client.api_secret:
        print("\nAPI Keys:    NOT SET (Set BINANCE_API_KEY and BINANCE_SECRET_KEY env vars)")
        return 0

    print("API Keys:    Configured (...%s)" % (client.api_key[-4:] if len(client.api_key) >= 4 else "Set"))
    try:
        acct = client.get_account()
        print("\nAccount Balances:")
        balances = acct.get("balances", []) if product == "SPOT" else acct.get("assets", [])
        has_positive = False
        for b in balances:
            asset = b.get("asset", "")
            free = float(b.get("free", 0.0) or b.get("availableBalance", 0.0) or 0.0)
            locked = float(b.get("locked", 0.0) or 0.0)
            clean_asset = str(asset).encode("ascii", errors="replace").decode("ascii")
            if free > 0.0001 or locked > 0.0001:
                print("  %-8s Free: %14.4f   Locked: %14.4f" % (clean_asset, free, locked))
                has_positive = True
        if not has_positive:
            print("  No positive asset balances found.")
    except Exception as e:
        print("Account query error: %s" % e)
    return 0


def cmd_positions(args, cfg):
    client = BinanceClient(product=cfg.get("product", "spot").lower(), testnet=cfg.get("paper", True))
    product = cfg.get("product", "spot").lower()
    if product == "futures":
        try:
            pos_risk = client.get_position_risk()
            open_pos = [p for p in pos_risk if abs(float(p.get("positionAmt", 0))) > 1e-6]
            if not open_pos:
                print("No open Futures positions.")
                return 0
            print("Open Futures Positions:")
            for p in open_pos:
                amt = float(p.get("positionAmt", 0))
                side = "LONG" if amt > 0 else "SHORT"
                print("  %-10s %s %10.4f  Entry: %10.2f  Mark: %10.2f  PnL: %+.2f USDT"
                      % (p.get("symbol"), side, abs(amt), float(p.get("entryPrice", 0)),
                         float(p.get("markPrice", 0)), float(p.get("unRealizedProfit", 0))))
        except Exception as e:
            print("Error fetching futures positions: %s" % e)
            return 1
    else:
        try:
            acct = client.get_account()
            balances = [b for b in acct.get("balances", []) if float(b.get("free", 0)) > 1e-6 or float(b.get("locked", 0)) > 1e-6]
            if not balances:
                print("No Spot balances holding assets.")
                return 0
            print("Spot Holdings:")
            for b in balances:
                clean_asset = str(b.get("asset", "")).encode("ascii", errors="replace").decode("ascii")
                print("  %-8s Free: %12.4f  Locked: %12.4f" % (clean_asset, float(b.get("free")), float(b.get("locked"))))
        except Exception as e:
            print("Error fetching spot balances: %s" % e)
            return 1
    return 0


def cmd_cancel(args, cfg):
    sym = args.symbol.upper()
    client = BinanceClient(product=cfg.get("product", "spot").lower(), testnet=cfg.get("paper", True))
    print("Canceling open orders for %s..." % sym)
    try:
        res = client.cancel_all_open_orders(sym)
        print("Cancel response: %s" % res)
        return 0
    except Exception as e:
        print("Error canceling orders: %s" % e)
        return 1


def cmd_flatten(args, cfg):
    sym = args.symbol.upper()
    market = cfg.get("product", "spot").lower()
    client = BinanceClient(product=market, testnet=cfg.get("paper", True))
    print("Triggering emergency bounded recovery for %s..." % sym)
    res = bounded_emergency_recovery(client, sym, market=market)
    print("Result: %s" % json.dumps(res, indent=2))
    return 0 if res.get("recovered") else 1


def cmd_reconcile(args, cfg):
    paper = cfg.get("paper", True)
    market = cfg.get("product", "spot").lower()
    client = BinanceClient(product=market, testnet=paper)
    sym = getattr(args, "symbol", None)
    print("Reconciling active orders with exchange (%s)..." % ("TESTNET" if paper else "LIVE"))
    res = reconcile_active_orders(client, symbol=sym)
    print("Reconciliation complete: checked=%d, updated=%d, active_remaining=%d"
          % (res["checked"], res["updated"], res["active_count"]))
    for r in res.get("results", []):
        prev = r.get("prev_status")
        new = r.get("new_status") or prev
        print("  - ID: %s (%s) %s -> %s" % (r.get("id"), r.get("symbol"), prev, new))
    return 0


def cmd_execute(args, cfg):
    orders_doc = load_json(ORDERS_PATH)
    if not orders_doc or not orders_doc.get("orders"):
        print("No orders to execute in %s" % ORDERS_PATH)
        return 0

    orders = orders_doc.get("orders", [])
    executed_count = 0
    for order in orders:
        sym = order.get("symbol")
        side = order.get("side", "buy").upper()
        qty = Decimal(str(order.get("qty", 0)))
        typ = order.get("type", "market").upper()
        entry = order.get("limit_price") or (order.get("plan") or {}).get("entry")
        stop = order.get("stop_loss_stop_price") or (order.get("plan") or {}).get("stop")
        target = order.get("take_profit_limit_price") or (order.get("plan") or {}).get("target")

        ret = execute_bracket_order(
            symbol=sym, side=side, qty=qty, entry=entry, stop=stop, target=target,
            order_type=typ, market=cfg.get("product", "spot"),
            dry_run=args.dry_run, confirm_live=args.confirm_live or args.live, cfg=cfg
        )
        if ret == 0:
            executed_count += 1
    print("\nProcessed %d order(s)." % executed_count)
    return 0


# =============================================================================
# MAIN CLI DISPATCHER
# =============================================================================

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    ap = argparse.ArgumentParser(description="Binance 24/7 Execution Desk (trader-broker)")
    # Direct order flags at top-level
    ap.add_argument("--symbol", help="Trading symbol (e.g. SOLUSDT)")
    ap.add_argument("--side", choices=["BUY", "SELL", "buy", "sell"], help="Order side: BUY or SELL")
    ap.add_argument("--qty", help="Quantity to trade")
    ap.add_argument("--entry", help="Entry price (omit for MARKET order)")
    ap.add_argument("--stop", help="Stop loss trigger price")
    ap.add_argument("--target", help="Take profit target price")
    ap.add_argument("--type", default="MARKET", choices=["MARKET", "LIMIT", "market", "limit"], help="Order type")
    ap.add_argument("--market", choices=["spot", "futures"], help="Market: spot or futures")
    ap.add_argument("--product", choices=["spot", "futures"], help="Alias for --market")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                    help="Default: simulate validations without transmitting network requests")
    ap.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                    help="Disable dry run and transmit real orders to exchange")
    ap.add_argument("--paper", "--testnet", dest="paper", action="store_true", default=None,
                    help="Route orders to Binance Testnet (paper trading)")
    ap.add_argument("--live-net", dest="paper", action="store_false",
                    help="Route orders to Binance Live endpoints (requires --confirm-live)")
    ap.add_argument("--confirm-live", action="store_true", help="Explicit confirmation for LIVE real-money trades")
    ap.add_argument("--live", action="store_true", help="Alias for --confirm-live")

    sub = ap.add_subparsers(dest="cmd")

    p_stat = sub.add_parser("status", help="Check Binance connection and account balance")
    p_stat.add_argument("--paper", "--testnet", dest="paper", action="store_true", default=None)
    p_stat.add_argument("--live-net", dest="paper", action="store_false")

    p_pos = sub.add_parser("positions", help="Show active positions and balances")
    p_pos.add_argument("--paper", "--testnet", dest="paper", action="store_true", default=None)
    p_pos.add_argument("--live-net", dest="paper", action="store_false")

    p_exec = sub.add_parser("execute", help="Execute approved orders from orders.json")
    p_exec.add_argument("--confirm-live", action="store_true", help="Explicit confirmation for LIVE trades")
    p_exec.add_argument("--live", action="store_true", help="Alias for --confirm-live")
    p_exec.add_argument("--dry-run", dest="dry_run", action="store_true", default=True, help="Default dry run")
    p_exec.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Disable dry run")
    p_exec.add_argument("--paper", "--testnet", dest="paper", action="store_true", default=None)
    p_exec.add_argument("--live-net", dest="paper", action="store_false")

    p_canc = sub.add_parser("cancel", help="Cancel open orders for a symbol")
    p_canc.add_argument("--symbol", required=True, help="Trading pair (e.g. SOLUSDT)")
    p_canc.add_argument("--paper", "--testnet", dest="paper", action="store_true", default=None)
    p_canc.add_argument("--live-net", dest="paper", action="store_false")

    p_flat = sub.add_parser("flatten", help="Trigger emergency bounded recovery for a symbol")
    p_flat.add_argument("--symbol", required=True, help="Trading pair (e.g. SOLUSDT)")
    p_flat.add_argument("--paper", "--testnet", dest="paper", action="store_true", default=None)
    p_flat.add_argument("--live-net", dest="paper", action="store_false")

    p_rec = sub.add_parser("reconcile", help="Reconcile active orders in journal with exchange status")
    p_rec.add_argument("--symbol", help="Trading pair filter (e.g. SOLUSDT)")
    p_rec.add_argument("--paper", "--testnet", dest="paper", action="store_true", default=None)
    p_rec.add_argument("--live-net", dest="paper", action="store_false")

    a = ap.parse_args(argv)
    cfg = load_json(CONFIG_PATH, {})
    if getattr(a, "paper", None) is not None:
        cfg["paper"] = a.paper

    # Top-level direct execution if --symbol is given
    if a.symbol and (a.side or a.entry or a.stop or a.target or a.qty):
        mkt = a.market or a.product or cfg.get("product", "spot")
        return execute_bracket_order(
            symbol=a.symbol,
            side=a.side or "BUY",
            qty=a.qty or "1.0",
            entry=a.entry,
            stop=a.stop,
            target=a.target,
            order_type=a.type or "MARKET",
            market=mkt,
            dry_run=a.dry_run,
            confirm_live=a.confirm_live or a.live,
            cfg=cfg
        )

    if not a.cmd:
        ap.print_help()
        return 1

    handlers = {
        "status": cmd_status,
        "positions": cmd_positions,
        "execute": cmd_execute,
        "cancel": cmd_cancel,
        "flatten": cmd_flatten,
        "reconcile": cmd_reconcile,
    }
    return handlers[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())

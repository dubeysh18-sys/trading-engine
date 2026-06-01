"""
rule_engine.py — The 4 Golden Rules Evaluator (In-Memory Version)

Evaluates rules on lists of candle dictionaries:
  - calculate_vwap(candles)           — Uses CLOSED candles only; anchors daily at 09:15
  - calculate_pivot(candles)          — Uses prior day candles only
  - calculate_resistances(pivot_data) — R1, R2, R3 from pivot data
  - evaluate_rules(...)               — Accepts closed_candles and live_price separately

Design principle (Bug-2 fix):
  All structural indicators (VWAP, pivot, wick analysis) are computed on
  FULLY CLOSED candles only.  The live LTP (the tick inside the current
  incomplete candle) is passed separately and used ONLY to measure the
  percentage distance from the established VWAP line.
"""

import logging

logger = logging.getLogger(__name__)

def calculate_vwap(candles):
    """
    Intraday VWAP — anchored to the latest trading day present in `candles`.

    IMPORTANT: Pass only FULLY CLOSED candles here.  The caller is responsible
    for excluding any still-open (incomplete) candle before calling this function.

    Anchor behaviour:
      - Filters candles to the most-recent calendar date in the list.
      - This ensures the VWAP resets at 09:15 AM each day without needing
        any external library (no pandas-ta required).

    Formula: sum(typical_price * volume) / sum(volume)
    """
    if not candles:
        return 0

    # Anchor: use only candles from the latest trading day (daily reset at 09:15)
    latest_day = max(c["timestamp"].date() for c in candles)
    today_candles = [c for c in candles if c["timestamp"].date() == latest_day]

    if not today_candles:
        return 0

    tp_vol = sum((c["high"] + c["low"] + c["close"]) / 3 * c["volume"] for c in today_candles)
    total_vol = sum(c["volume"] for c in today_candles)

    return tp_vol / total_vol if total_vol > 0 else 0


def calculate_pivot(candles):
    """
    Find yesterday's high, low, close and calculate standard Pivot Point.
    Groups all candles before the latest day as 'yesterday'.
    """
    if not candles:
        return None

    latest_day = max(c["timestamp"].date() for c in candles)
    prev_day_candles = [c for c in candles if c["timestamp"].date() < latest_day]

    if not prev_day_candles:
        # Fallback: use first half of candles
        mid = len(candles) // 2
        prev_day_candles = candles[:mid]

    if not prev_day_candles:
        return None

    high  = max(c["high"] for c in prev_day_candles)
    low   = min(c["low"] for c in prev_day_candles)
    close = prev_day_candles[-1]["close"]

    pivot = (high + low + close) / 3
    return {
        "pivot": pivot,
        "high": high,
        "low": low
    }


def calculate_resistances(pivot_data):
    """Calculate resistance levels R1, R2, R3 from pivot point data."""
    if not pivot_data:
        return 0, 0, 0
    p = pivot_data["pivot"]
    low = pivot_data["low"]
    high = pivot_data["high"]

    r1 = (2 * p) - low
    r2 = p + (high - low)
    r3 = high + 2 * (p - low)

    return r1, r2, r3


def evaluate_rules(stock, current_candle, closed_candles, nifty_status, trigger_time, live_price=None):
    """
    Apply the 4 Golden Rules in priority order.

    Args:
        stock          : Symbol string (for logging).
        current_candle : The LAST FULLY CLOSED 5-min candle at trigger time.
                         Used for wick analysis and green/red colour check.
        closed_candles : All FULLY CLOSED candles up to and including
                         `current_candle`.  The incomplete live candle must
                         NOT be in this list (Bug-2 fix).
        nifty_status   : Dict with keys ltp, vwap, is_bullish.
        trigger_time   : Naive IST datetime of the Chartink alert.
        live_price     : (optional) Live LTP tick from the API.  When provided,
                         this is used for the VWAP distance check (Rule 3)
                         instead of the closed candle's close price, giving a
                         more accurate real-time reading.

    Returns:
        (verdict, reason) — verdict is one of: ENTER / SKIP / WAIT / ERROR
    """
    if not current_candle or not closed_candles:
        return "ERROR", "Insufficient candle data"

    # Structural price: use live LTP if provided, else last closed candle's close
    reference_price = live_price if live_price else current_candle["close"]

    # All structural indicators are derived from CLOSED candles only
    vwap  = calculate_vwap(closed_candles)
    pivot = calculate_pivot(closed_candles)
    r1, r2, r3 = calculate_resistances(pivot)
    y_high = pivot["high"] if pivot else 0

    # ── RULE 1: Ceiling Guard ──────────────────────────────────────────
    # Skip if live price is within 0.3% below R1, R2, R3, or Yesterday's High
    ceilings = [val for val in (r1, r2, r3, y_high) if val and val > 0]
    for ceiling in ceilings:
        if ceiling * 0.997 <= reference_price < ceiling:
            return "SKIP", f"Hitting The Ceiling (within 0.3% below resistance {ceiling:.2f})"

    # ── RULE 2: Market Wind ────────────────────────────────────────────
    # Skip if NIFTY is bearish (LTP below NIFTY VWAP)
    if nifty_status and not nifty_status.get("is_bullish", True):
        return "SKIP", "Market Wind is against you (NIFTY below VWAP)"

    # ── RULE 3: Base Camp ──────────────────────────────────────────────
    # Wait if price is overextended >0.8% above VWAP.
    # Uses live_price (the current tick) so the check reflects the real-time
    # distance from the established VWAP line, not the stale closed-candle price.
    if vwap and reference_price > (vwap * 1.008):
        pct_above = ((reference_price - vwap) / vwap) * 100
        return "WAIT", f"Base Camp: Overextended {pct_above:.2f}% above VWAP ({vwap:.2f}). Wait for pullback."

    # ── RULE 4: Wick Rejection & Color ─────────────────────────────────
    # Wick analysis uses the LAST CLOSED candle — never the live tick.
    upper_wick = current_candle["high"] - max(current_candle["open"], current_candle["close"])
    solid_body = abs(current_candle["open"] - current_candle["close"])

    if upper_wick > solid_body:
        return "SKIP", "The Wick: Massive upper wick (rejection) larger than solid body"
    if current_candle["close"] <= current_candle["open"]:
        return "SKIP", "Not a solid green close"

    # All rules pass
    return "ENTER", "All conditions met"

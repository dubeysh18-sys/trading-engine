"""
rule_engine.py — The 4 Golden Rules Evaluator (In-Memory Version)

Evaluates rules on lists of candle dictionaries:
  - calculate_vwap()
  - calculate_pivot()
  - calculate_resistances()
  - evaluate_rules()
"""

import logging

logger = logging.getLogger(__name__)

def calculate_vwap(candles):
    """
    Intraday VWAP — resets at the start of the latest trading day in candles.
    Formula: cumulative(price * volume) / cumulative(volume)
    """
    if not candles:
        return 0

    # Resets VWAP on the latest day in candles
    latest_day = max(c["timestamp"].date() for c in candles)
    today_candles = [c for c in candles if c["timestamp"].date() == latest_day]

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


def evaluate_rules(stock, current_candle, all_candles, nifty_status, trigger_time):
    """
    Apply the 4 Golden Rules in priority order.

    Returns:
        (verdict, reason)
    """
    if not current_candle or not all_candles:
        return "ERROR", "Insufficient candle data"

    current_price = current_candle["close"]
    vwap = calculate_vwap(all_candles)
    pivot = calculate_pivot(all_candles)
    r1, r2, r3 = calculate_resistances(pivot)
    y_high = pivot["high"] if pivot else 0

    # ── RULE 1: Ceiling Guard ──────────────────────────────────────────
    # Skip if current price is within 0.3% below R1, R2, R3, or Yesterday's High
    ceilings = [val for val in (r1, r2, r3, y_high) if val and val > 0]
    for ceiling in ceilings:
        if ceiling * 0.997 <= current_price < ceiling:
            return "SKIP", f"Hitting The Ceiling (within 0.3% below resistance {ceiling:.2f})"

    # ── RULE 2: Market Wind ────────────────────────────────────────────
    # Skip if NIFTY is bearish (LTP below NIFTY VWAP)
    if nifty_status and not nifty_status.get("is_bullish", True):
        return "SKIP", "Market Wind is against you (NIFTY below VWAP)"

    # ── RULE 3: Base Camp ──────────────────────────────────────────────
    # Wait if price is overextended >0.8% above VWAP
    if vwap and current_price > (vwap * 1.008):
        return "WAIT", "Base Camp: Overextended >0.8% above VWAP. Wait for pullback to VWAP."

    # ── RULE 4: Wick Rejection & Color ─────────────────────────────────
    # Skip if massive upper wick (larger than solid body) or not a green candle
    upper_wick = current_candle["high"] - max(current_candle["open"], current_candle["close"])
    solid_body = abs(current_candle["open"] - current_candle["close"])

    if upper_wick > solid_body:
        return "SKIP", "The Wick: Massive upper wick (rejection) larger than solid body"
    if current_candle["close"] <= current_candle["open"]:
        return "SKIP", "Not a solid green close"

    # All rules pass
    return "ENTER", "All conditions met"

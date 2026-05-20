"""
logic_engine.py — The 4 Golden Rules Evaluator

Takes a stock's OHLCV DataFrame + NIFTY data and returns:
  - Calculated indicators (VWAP, 9 EMA, Pivot Points, candle anatomy)
  - A verdict: ENTER | WAIT | SKIP
  - Entry price, target, stop loss
"""

import logging
import pandas as pd
from ta.trend import EMAIndicator

logger = logging.getLogger(__name__)

# ── Indicator Calculations ─────────────────────────────────────────────────────

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Intraday VWAP — resets at the start of each trading day.
    Formula: cumulative(price * volume) / cumulative(volume)
    where price = (high + low + close) / 3
    """
    df = df.copy()
    df["date"] = df["timestamp"].dt.date
    df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["typical_price"] * df["volume"]

    # Group by day to reset VWAP each day
    df["cum_tp_vol"] = df.groupby("date")["tp_vol"].cumsum()
    df["cum_vol"]    = df.groupby("date")["volume"].cumsum()
    df["vwap"]       = df["cum_tp_vol"] / df["cum_vol"]
    return df["vwap"]


def calculate_pivot_points(prev_day_df: pd.DataFrame) -> dict:
    """
    Standard pivot points from the previous trading day's H/L/C.
    Returns: {pivot, r1, r2, s1, s2}
    """
    if prev_day_df.empty:
        return {}

    high  = prev_day_df["high"].max()
    low   = prev_day_df["low"].min()
    close = prev_day_df["close"].iloc[-1]

    pivot = (high + low + close) / 3
    r1 = (2 * pivot) - low
    r2 = pivot + (high - low)
    r3 = high + 2 * (pivot - low)
    s1 = (2 * pivot) - high
    s2 = pivot - (high - low)

    return {"pivot": pivot, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "yesterday_high": high}


def get_indicator_snapshot(df: pd.DataFrame) -> dict:
    """
    Calculate all indicators needed for rule evaluation.

    Returns a dict with:
      - vwap, ema9
      - pivot points (r1, r2, s1)
      - trigger candle anatomy (upper_wick, solid_body)
      - latest candle OHLCV
    """
    if df.empty or len(df) < 10:
        return {}

    df = df.copy().reset_index(drop=True)

    # ── VWAP ──────────────────────────────────────────────────
    df["vwap"] = calculate_vwap(df)

    # ── 9 EMA ─────────────────────────────────────────────────
    df["ema9"] = EMAIndicator(close=df["close"], window=9).ema_indicator()

    # ── Pivot Points (previous trading day) ───────────────────
    today = df["timestamp"].dt.date.max()
    prev_day = df[df["timestamp"].dt.date < today]
    if prev_day.empty:
        # If only 1 day of data, use first half as "previous"
        mid = len(df) // 2
        prev_day = df.iloc[:mid]

    pivots = calculate_pivot_points(prev_day)

    # ── Latest (trigger) candle ───────────────────────────────
    latest = df.iloc[-1]
    open_  = latest["open"]
    high   = latest["high"]
    low    = latest["low"]
    close  = latest["close"]

    upper_wick  = high - max(open_, close)
    solid_body  = abs(open_ - close)

    return {
        "vwap":        round(latest["vwap"], 2) if pd.notna(latest["vwap"]) else None,
        "ema9":        round(latest["ema9"], 2) if pd.notna(latest["ema9"]) else None,
        "pivot_r1":    round(pivots.get("r1", 0), 2),
        "pivot_r2":    round(pivots.get("r2", 0), 2),
        "pivot_r3":    round(pivots.get("r3", 0), 2),
        "yesterday_high": round(pivots.get("yesterday_high", 0), 2),
        "pivot_s1":    round(pivots.get("s1", 0), 2),
        "upper_wick":  round(upper_wick, 4),
        "solid_body":  round(solid_body, 4),
        "latest_open":  open_,
        "latest_high":  high,
        "latest_low":   low,
        "latest_close": close,
    }


# ── The 4 Golden Rules ─────────────────────────────────────────────────────────

def evaluate_rules(
    stock_df: pd.DataFrame,
    nifty_df: pd.DataFrame,
    trigger_price: float | None = None
) -> dict:
    """
    Apply the 4 Golden Rules in priority order.

    Args:
        stock_df:      5-min OHLCV DataFrame for the stock (last 3 days)
        nifty_df:      5-min OHLCV DataFrame for NIFTY 50 (last 3 days)
        trigger_price: Price from Chartink alert (used as "current price")

    Returns dict with:
        verdict, verdict_reason, entry, target, stop_loss,
        and all indicator values
    """
    indicators = get_indicator_snapshot(stock_df)
    nifty_indicators = get_indicator_snapshot(nifty_df)

    if not indicators:
        return {
            "verdict": "ERROR",
            "verdict_reason": "Insufficient data to calculate indicators",
            "entry": None, "target": None, "stop_loss": None,
            **{k: None for k in ["vwap", "ema9", "pivot_r1", "pivot_r2",
                                   "pivot_s1", "upper_wick", "solid_body",
                                   "nifty_ltp", "nifty_vwap"]}
        }

    # Current price: prefer Chartink trigger_price, fallback to latest close
    current_price = trigger_price if trigger_price else indicators["latest_close"]
    vwap     = indicators["vwap"]
    ema9     = indicators["ema9"]
    r1       = indicators["pivot_r1"]
    r2       = indicators["pivot_r2"]
    r3       = indicators.get("pivot_r3")
    y_high   = indicators.get("yesterday_high")
    upper_wick = indicators["upper_wick"]
    solid_body = indicators["solid_body"]
    close_price = indicators["latest_close"]
    open_price = indicators["latest_open"]

    nifty_ltp  = nifty_indicators.get("latest_close")
    nifty_vwap = nifty_indicators.get("vwap")

    verdict        = "ENTER"
    verdict_reason = "All 4 rules passed — clear entry signal"

    # ── Rule 1: The Ceiling (Pivots) ──────────────────────────
    # Is current price within 0.3% below a Pivot line (R1, R2, R3) or Yesterday's High?
    ceilings = [val for val in (r1, r2, r3, y_high) if val and val > 0]
    is_hitting_ceiling = False
    for ceiling in ceilings:
        if ceiling * 0.997 <= current_price < ceiling:
            is_hitting_ceiling = True
            break
            
    if is_hitting_ceiling:
        verdict        = "SKIP"
        verdict_reason = "Hitting The Ceiling (within 0.3% below a Pivot or Y-High)"

    # ── Rule 2: Market Wind (Index) ───────────────────────────
    elif nifty_ltp and nifty_vwap and nifty_ltp < nifty_vwap:
        verdict        = "SKIP"
        verdict_reason = "Market Wind is against you (NIFTY below VWAP)"

    # ── Rule 3: Base Camp (The "Kiss") ────────────────────────
    elif ema9 and current_price > (ema9 * 1.01): # Arbitrary >1% gap above EMA9 is a spike
        verdict        = "WAIT"
        verdict_reason = "Base Camp: Vertical spike far above 9 EMA. Wait for pullback kiss."

    # ── Rule 4: The Wick (Price Action) ───────────────────────
    elif upper_wick > solid_body:
        verdict        = "SKIP"
        verdict_reason = "The Wick: Massive upper wick (rejection) larger than solid body"
    elif close_price <= open_price:
        verdict        = "SKIP"
        verdict_reason = "Not a solid green close"

    # ── Trade Parameters (only on ENTER) ──────────────────────
    entry     = round(current_price, 2) if verdict == "ENTER" else None
    stop_loss = None
    target    = None

    if verdict == "ENTER":
        # Target: Next closest ceiling above entry
        higher_ceilings = [c for c in ceilings if c > current_price]
        if higher_ceilings:
            target = round(min(higher_ceilings), 2)
        else:
            # If breaking out above all known ceilings, target +1.5%
            target = round(current_price * 1.015, 2)

        # Stop Loss: MAX(VWAP - 0.1%, low of trigger candle, or EMA9)
        vwap_sl = round(vwap * 0.999, 2) if vwap else None
        candle_low_sl = indicators.get("latest_low")
        ema_sl = round(ema9 * 0.999, 2) if ema9 else None
        
        possible_sls = [val for val in (vwap_sl, candle_low_sl, ema_sl) if val is not None]
        if possible_sls:
            stop_loss = round(max(possible_sls), 2)
        else:
            stop_loss = round(current_price * 0.99, 2) # fallback 1%

    return {
        "verdict":        verdict,
        "verdict_reason": verdict_reason,
        "entry":          entry,
        "target":         target,
        "stop_loss":      stop_loss,
        "vwap":           vwap,
        "ema9":           indicators.get("ema9"),
        "pivot_r1":       r1,
        "pivot_r2":       r2,
        "pivot_s1":       indicators.get("pivot_s1"),
        "upper_wick":     upper_wick,
        "solid_body":     solid_body,
        "nifty_ltp":      nifty_ltp,
        "nifty_vwap":     nifty_vwap,
    }

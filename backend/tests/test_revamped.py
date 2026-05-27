import pytest
from datetime import datetime, date, time
import pytz
from fastapi.testclient import TestClient

from rule_engine import calculate_vwap, calculate_pivot, calculate_resistances, evaluate_rules
from main import app, parse_time_to_ist, is_market_hours, get_candle_at_time

client = TestClient(app)

# ── HELPER DATA GENERATORS ──────────────────────────────────────────────────
def make_candle(dt, open_p, high, low, close, volume):
    return {
        "timestamp": dt,
        "open": float(open_p),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": int(volume),
    }

# ── indicator TESTS ─────────────────────────────────────────────────────────
def test_calculate_vwap():
    # Intraday VWAP resets daily
    IST = pytz.timezone("Asia/Kolkata")
    dt1 = datetime(2026, 5, 26, 9, 15)
    dt2 = datetime(2026, 5, 26, 9, 20)
    dt3 = datetime(2026, 5, 27, 9, 15) # next day

    candles = [
        make_candle(dt1, 100, 105, 95, 100, 10),  # Typical price = (105 + 95 + 100)/3 = 100. Typical price * volume = 1000
        make_candle(dt2, 102, 104, 100, 102, 20), # Typical price = (104 + 100 + 102)/3 = 102. Typical price * volume = 2040
        make_candle(dt3, 200, 205, 195, 200, 10), # resets here! Typical price = 200 * 10 = 2000
    ]

    # Calculate VWAP - should only include latest day (2026-05-27)
    vwap = calculate_vwap(candles)
    assert vwap == 200.0

    # If we only pass day 1 candles:
    vwap_day1 = calculate_vwap(candles[:2])
    # total typ * vol = 1000 + 2040 = 3040
    # total vol = 30
    assert abs(vwap_day1 - (3040 / 30)) < 0.01


def test_calculate_pivot_and_resistances():
    dt1 = datetime(2026, 5, 26, 9, 15)
    dt2 = datetime(2026, 5, 26, 15, 30)
    dt3 = datetime(2026, 5, 27, 9, 15)

    candles = [
        make_candle(dt1, 100, 110, 90, 105, 10),
        make_candle(dt2, 105, 120, 100, 115, 10),
        make_candle(dt3, 115, 118, 114, 116, 10),
    ]

    pivot_data = calculate_pivot(candles)
    assert pivot_data is not None
    # 'yesterday' candles are dt1 and dt2 (before 27th)
    # yesterday high = 120, low = 90, close = 115
    assert pivot_data["high"] == 120
    assert pivot_data["low"] == 90
    # pivot = (120 + 90 + 115) / 3 = 325 / 3 = 108.333
    assert abs(pivot_data["pivot"] - 108.33) < 0.05

    r1, r2, r3 = calculate_resistances(pivot_data)
    # r1 = (2 * pivot) - low
    # r2 = pivot + (high - low)
    # r3 = high + 2 * (pivot - low)
    p = pivot_data["pivot"]
    assert abs(r1 - (2 * p - 90)) < 0.01
    assert abs(r2 - (p + 30)) < 0.01
    assert abs(r3 - (120 + 2 * (p - 90))) < 0.01


# ── RULE EVALUATION TESTS ───────────────────────────────────────────────────
def test_evaluate_rules_all_pass():
    dt = datetime(2026, 5, 27, 10, 0)
    current_candle = make_candle(dt, 100, 105, 99, 104, 10)
    
    # Previous day high/low/close leads to pivot parameters
    prev_dt = datetime(2026, 5, 26, 15, 0)
    all_candles = [
        make_candle(prev_dt, 90, 95, 85, 90, 10), # Pivot data: high=95, low=85, close=90 -> p=90
        current_candle
    ]
    # R1 = 2*90 - 85 = 95. R2 = 90 + 10 = 100. R3 = 95 + 2*5 = 105.
    # Current close = 104, not within 0.3% below R1(95), R2(100), R3(105) or yesterday's high(95).
    # Wait, 105 * 0.997 = 104.685. So 104 is below 104.685, which is not within 0.3% of R3.
    # NIFTY bullish status is True
    nifty_status = {"is_bullish": True}
    
    # VWAP = Typical price of day 2 candle = (100 + 105 + 99)/3 = 101.33.
    # 1.008 * VWAP = 102.14. Wait, current close 104 is > 102.14, so it should trigger WAIT!
    # Let's adjust current candle close so it is not overextended: close = 101.5
    current_candle_ok = make_candle(dt, 100, 102, 99.5, 101.5, 10)
    # VWAP typical price = (100 + 102 + 99.5)/3 = 100.5. 1.008 * VWAP = 101.3.
    # Let's make VWAP higher by adding a volume/price weight, or just increase VWAP.
    # Let's test ENTER verdict by ensuring close is within bounds:
    # Let's set VWAP to 101: if typical price is 101, 1.008 * 101 = 101.8. Current close 101.5 is <= 101.8.
    
    all_candles_ok = [
        make_candle(prev_dt, 90, 95, 85, 90, 10),
        current_candle_ok
    ]

    verdict, reason = evaluate_rules(
        stock="TEST",
        current_candle=current_candle_ok,
        all_candles=all_candles_ok,
        nifty_status=nifty_status,
        trigger_time=dt
    )
    assert verdict == "ENTER"


def test_evaluate_rules_ceiling_guard():
    dt = datetime(2026, 5, 27, 10, 0)
    prev_dt = datetime(2026, 5, 26, 15, 0)
    # Pivot: high=100, low=90, close=95 -> p=95.
    # R1 = 2*95 - 90 = 100.
    # Ceiling is R1 = 100. 0.3% below is 99.7.
    # If close is 99.8, it should skip due to Ceiling Guard.
    current_candle = make_candle(dt, 98, 100, 97, 99.8, 10)
    all_candles = [
        make_candle(prev_dt, 90, 100, 90, 95, 10),
        current_candle
    ]
    nifty_status = {"is_bullish": True}
    verdict, reason = evaluate_rules("TEST", current_candle, all_candles, nifty_status, dt)
    assert verdict == "SKIP"
    assert "Ceiling" in reason


def test_evaluate_rules_market_wind():
    dt = datetime(2026, 5, 27, 10, 0)
    prev_dt = datetime(2026, 5, 26, 15, 0)
    current_candle = make_candle(dt, 100, 102, 99, 101, 10)
    all_candles = [
        make_candle(prev_dt, 90, 95, 85, 90, 10),
        current_candle
    ]
    # NIFTY is bearish
    nifty_status = {"is_bullish": False}
    verdict, reason = evaluate_rules("TEST", current_candle, all_candles, nifty_status, dt)
    assert verdict == "SKIP"
    assert "Market Wind" in reason


def test_evaluate_rules_base_camp():
    dt = datetime(2026, 5, 27, 10, 0)
    prev_dt = datetime(2026, 5, 26, 15, 0)
    # VWAP will be around 100
    current_candle = make_candle(dt, 100, 103, 99, 102.5, 10)
    all_candles = [
        make_candle(prev_dt, 90, 92, 88, 90, 10),
        current_candle
    ]
    # 102.5 is > 100.8% of typical price (which is ~100.6)
    nifty_status = {"is_bullish": True}
    verdict, reason = evaluate_rules("TEST", current_candle, all_candles, nifty_status, dt)
    assert verdict == "WAIT"
    assert "Base Camp" in reason


def test_evaluate_rules_wick_rejection():
    dt = datetime(2026, 5, 27, 10, 0)
    prev_dt = datetime(2026, 5, 26, 15, 0)
    # Open=100, Close=101, High=105 -> upper wick = 4. Body = 1.
    # Wick rejection (upper wick > body)
    current_candle = make_candle(dt, 100, 105, 99, 101, 10)
    all_candles = [
        make_candle(prev_dt, 90, 92, 88, 90, 10),
        current_candle
    ]
    nifty_status = {"is_bullish": True}
    verdict, reason = evaluate_rules("TEST", current_candle, all_candles, nifty_status, dt)
    assert verdict == "SKIP"
    assert "Massive upper wick" in reason


# ── UTILITY TESTS ───────────────────────────────────────────────────────────
def test_parse_time_to_ist():
    dt = parse_time_to_ist("02:30 pm")
    assert dt.hour == 14
    assert dt.minute == 30

    dt_24 = parse_time_to_ist("11:15")
    assert dt_24.hour == 11
    assert dt_24.minute == 15


def test_is_market_hours():
    # Mon = 0, Wed = 2, Sat = 5
    dt_weekday_in = datetime(2026, 5, 27, 10, 0) # Wednesday, 10:00
    assert is_market_hours(dt_weekday_in) is True

    dt_weekday_out = datetime(2026, 5, 27, 8, 30) # Wednesday, 8:30
    assert is_market_hours(dt_weekday_out) is False

    dt_weekend = datetime(2026, 5, 30, 11, 0) # Saturday, 11:00
    assert is_market_hours(dt_weekend) is False


def test_get_candle_at_time():
    t1 = datetime(2026, 5, 27, 9, 15)
    t2 = datetime(2026, 5, 27, 9, 20)
    t3 = datetime(2026, 5, 27, 9, 25)

    candles = [
        {"timestamp": t1, "val": 1},
        {"timestamp": t2, "val": 2},
        {"timestamp": t3, "val": 3},
    ]

    # Exactly matches t2
    c = get_candle_at_time(candles, t2)
    assert c["val"] == 2

    # Between t2 and t3 -> should return t2 (the latest candle <= target)
    c_mid = get_candle_at_time(candles, datetime(2026, 5, 27, 9, 22))
    assert c_mid["val"] == 2


# ── API ENDPOINT TESTS ──────────────────────────────────────────────────────
def test_health_endpoint():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

import pytest
import os
import pandas as pd
from upstox_client import get_historical_data
from logic_engine import calculate_vwap, calculate_pivot_points
from ta.trend import EMAIndicator

def test_upstox_indicators_realtime_correctness():
    """
    Live Integration Test: Ensures Upstox API is returning mathematically sound
    real-time indicators (VWAP, EMA, Pivots) for a liquid asset (NIFTY 50).
    """
    if not os.getenv("UPSTOX_ACCESS_TOKEN"):
        pytest.skip("UPSTOX_ACCESS_TOKEN not set. Skipping live API test.")
        
    df = get_historical_data("RELIANCE", days=2)
    assert df is not None, "Failed to fetch historical data from Upstox"
    assert not df.empty, "DataFrame is empty"
    assert "timestamp" in df.columns
    assert "close" in df.columns
    assert "volume" in df.columns

    # 1. Test VWAP mathematical logic
    # VWAP should be roughly near the trading range
    vwap_series = calculate_vwap(df)
    assert not vwap_series.isna().all(), "VWAP returned all NaNs"
    last_vwap = vwap_series.iloc[-1]
    last_close = df.iloc[-1]["close"]
    
    # VWAP shouldn't deviate wildly from current price (e.g., > 5% difference is extremely rare intraday)
    diff_pct = abs(last_vwap - last_close) / last_close * 100
    assert diff_pct < 5.0, f"VWAP ({last_vwap}) deviates wildly from LTP ({last_close})"
    
    # 2. Test EMA mathematical logic
    ema = EMAIndicator(close=df["close"], window=9).ema_indicator()
    assert not ema.isna().all(), "EMA returned all NaNs"
    last_ema = ema.iloc[-1]
    diff_pct_ema = abs(last_ema - last_close) / last_close * 100
    assert diff_pct_ema < 5.0, f"EMA9 ({last_ema}) deviates wildly from LTP ({last_close})"

    # 3. Test Pivot Points logic
    # Separate today and yesterday
    today = df["timestamp"].dt.date.max()
    yesterday_df = df[df["timestamp"].dt.date < today]
    
    if not yesterday_df.empty:
        pivots = calculate_pivot_points(yesterday_df)
        assert "pivot" in pivots
        assert "r1" in pivots
        assert "s1" in pivots
        
        # Fundamental Pivot rule: R1 > Pivot > S1
        assert pivots["r1"] > pivots["pivot"], "R1 must be greater than Pivot"
        assert pivots["pivot"] > pivots["s1"], "Pivot must be greater than S1"

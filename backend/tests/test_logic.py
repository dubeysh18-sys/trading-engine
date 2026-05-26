import pytest
import pandas as pd
from logic_engine import evaluate_wait_upgrade

def test_evaluate_wait_upgrade_failure():
    """Negative Testing: Test if a stock that crashes below VWAP support is correctly cancelled."""
    # Failure level is vwap * 0.997
    last_candle = pd.Series({
        'open': 100,
        'high': 100,
        'low': 99,
        'close': 99.6,  # 0.4% below VWAP (Threshold is 0.3%)
        'vwap': 100
    })
    
    result = evaluate_wait_upgrade(last_candle, 100.5)
    assert result["status"] == "SKIP"
    assert "CANCELLED" in result["reason"]

def test_evaluate_wait_upgrade_not_touched_zone():
    """Boundary Value Analysis: Did not touch the zone."""
    last_candle = pd.Series({
        'open': 101,
        'high': 101.5,
        'low': 100.4,  # VWAP is 100. Upper zone is 100.3. Low is > 100.3.
        'close': 100.5,
        'vwap': 100
    })
    result = evaluate_wait_upgrade(last_candle, 101.0)
    assert result["status"] == "WAIT"
    assert "Has not tested VWAP support zone yet" in result["reason"]

def test_evaluate_wait_upgrade_base_candle_failed():
    """Negative Testing: Touched zone, but closed red with no wick."""
    last_candle = pd.Series({
        'open': 100.2,
        'high': 100.2,
        'low': 99.9,
        'close': 99.9, # Red candle (close < open). Wick = min(100.2, 99.9) - 99.9 = 0. Body = 0.3
        'vwap': 100
    })
    result = evaluate_wait_upgrade(last_candle, 100.0)
    assert result["status"] == "WAIT"
    assert "no buyer conviction" in result["reason"]

def test_evaluate_wait_upgrade_success_but_waiting_for_trigger():
    """Positive/Negative Testing: Formed base candle, but live price hasn't broken high."""
    last_candle = pd.Series({
        'open': 99.9,
        'high': 100.1,
        'low': 99.9,
        'close': 100.0, # Green candle
        'vwap': 100
    })
    result = evaluate_wait_upgrade(last_candle, 100.0) # Live price = 100.0 (needs to be > 100.1)
    assert result["status"] == "WAIT"
    assert "Waiting for live price to break above" in result["reason"]

def test_evaluate_wait_upgrade_full_success():
    """Happy Path: Formed base candle and live price broke high."""
    last_candle = pd.Series({
        'open': 99.9,
        'high': 100.1,
        'low': 99.9,
        'close': 100.0, # Green candle, touched zone
        'vwap': 100
    })
    result = evaluate_wait_upgrade(last_candle, 100.2) # Live price broke high (100.2 > 100.1)
    assert result["status"] == "ENTER"
    assert result["entry_price"] == 100.2
    assert result["stop_loss"] == 99.9  # min(VWAP(100), low(99.9)) = 99.9

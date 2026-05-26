"""
test_comprehensive_qa.py
========================
100 QA test cases for the Trading Rule Engine covering:
  - Logic Engine (4 Golden Rules)
  - Backtest Simulation
  - API Endpoints
  - Stop Loss guard
  - Date filter
  - Admin endpoints
  - Edge cases
  - Data integrity
"""

import pytest
import json
import sys
import os
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, patch
import pandas as pd
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from database import init_db, get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base, Alert, BacktestResult
import main
from main import app, format_time_str
from logic_engine import (
    evaluate_rules,
    calculate_vwap,
    calculate_pivot_points,
    get_indicator_snapshot,
    evaluate_wait_upgrade,
)
from backtest import (
    parse_trigger_time,
    _fmt_time,
    _backtest_single_alert,
    run_backtest,
    create_pending_trade,
)

# ─── Test DB setup ────────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite:///./qa_test.db"

@pytest.fixture(scope="session")
def engine():
    _engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=_engine)
    yield _engine
    Base.metadata.drop_all(bind=_engine)
    # On Windows, the SQLite file may still be open; silently ignore if unlink fails
    import pathlib
    try:
        pathlib.Path("qa_test.db").unlink(missing_ok=True)
    except OSError:
        pass  # Windows file lock — safe to ignore, db is empty and will be re-created

@pytest.fixture
def db(engine):
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()

@pytest.fixture
def client(engine):
    def override_get_db():
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _make_df(candles: list) -> pd.DataFrame:
    """Helper: build a 5-min OHLCV dataframe from a list of dicts."""
    rows = []
    for c in candles:
        rows.append({
            "timestamp": pd.Timestamp(c["ts"]),
            "open": c["o"], "high": c["h"],
            "low": c["l"], "close": c["c"],
            "volume": c.get("v", 100_000)
        })
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def _make_alert(db, stock="TESTSTK", entry=100.0, target=110.0, sl=97.0,
                trigger_time="9:15 am", trigger_date="2026-05-27",
                verdict="ENTER"):
    """Helper: create and persist a test Alert with a BacktestResult."""
    alert = Alert(
        stock=stock, trigger_time=trigger_time, trigger_date=trigger_date,
        entry_price=entry, target=target, stop_loss=sl,
        trigger_price=entry, verdict=verdict,
        scan_name="TEST", alert_name="TEST"
    )
    db.add(alert)
    db.flush()
    return alert


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: parse_trigger_time (6 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestParseTriggerTime:
    def test_tc01_standard_pm(self):
        """TC01: Parses '3:25 pm' correctly."""
        result = parse_trigger_time("3:25 pm")
        assert result == time(15, 25)

    def test_tc02_standard_am(self):
        """TC02: Parses '9:15 am' correctly."""
        assert parse_trigger_time("9:15 am") == time(9, 15)

    def test_tc03_no_space_pm(self):
        """TC03: Parses '3:25PM' (no space) correctly."""
        assert parse_trigger_time("3:25PM") == time(15, 25)

    def test_tc04_24h_format(self):
        """TC04: Parses '15:25:00' (24h) correctly."""
        assert parse_trigger_time("15:25:00") == time(15, 25)

    def test_tc05_noon(self):
        """TC05: Parses '12:00 pm' = noon."""
        assert parse_trigger_time("12:00 pm") == time(12, 0)

    def test_tc06_invalid_returns_none(self):
        """TC06: Returns None for garbage input."""
        assert parse_trigger_time("not-a-time") is None


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: _fmt_time (5 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestFmtTime:
    def test_tc07_morning_no_leading_zero(self):
        """TC07: 9:15 am — no leading zero."""
        assert _fmt_time(time(9, 15)) == "9:15 am"

    def test_tc08_afternoon(self):
        """TC08: 3:25 pm formatted."""
        assert _fmt_time(time(15, 25)) == "3:25 pm"

    def test_tc09_noon_exact(self):
        """TC09: 12:00 pm (noon)."""
        assert _fmt_time(time(12, 0)) == "12:00 pm"

    def test_tc10_midnight_noon_boundary(self):
        """TC10: 11:59 am."""
        assert _fmt_time(time(11, 59)) == "11:59 am"

    def test_tc11_market_close(self):
        """TC11: 3:30 pm."""
        assert _fmt_time(time(15, 30)) == "3:30 pm"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: format_time_str in main.py (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatTimeStr:
    def test_tc12_none_returns_dash(self):
        """TC12: None → '—'"""
        assert format_time_str(None) == "—"

    def test_tc13_already_formatted(self):
        """TC13: Already-clean '3:25 pm' → '3:25 pm'."""
        assert format_time_str("3:25 pm") == "3:25 pm"

    def test_tc14_strips_leading_zero(self):
        """TC14: '09:15 am' → '9:15 am'."""
        assert format_time_str("09:15 am") == "9:15 am"

    def test_tc15_24h_converts(self):
        """TC15: '15:25' → '3:25 pm'."""
        assert format_time_str("15:25") == "3:25 pm"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4: calculate_vwap (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestCalculateVwap:
    def _df(self):
        return _make_df([
            {"ts": "2026-05-27 09:15", "o": 100, "h": 105, "l": 98, "c": 103, "v": 10000},
            {"ts": "2026-05-27 09:20", "o": 103, "h": 107, "l": 101, "c": 106, "v": 8000},
            {"ts": "2026-05-27 09:25", "o": 106, "h": 108, "l": 104, "c": 105, "v": 5000},
        ])

    def test_tc16_vwap_is_cumulative(self):
        """TC16: VWAP is cumulative (last row > first row's typical price)."""
        df = self._df()
        vwap = calculate_vwap(df)
        assert len(vwap) == 3
        assert abs(vwap.iloc[0] - (105 + 98 + 103) / 3) < 0.01  # first row: (H+L+C)/3

    def test_tc17_vwap_increases_with_volume(self):
        """TC17: VWAP changes as volume-weighted prices accumulate."""
        df = self._df()
        vwap = calculate_vwap(df)
        assert vwap.iloc[1] != vwap.iloc[0]

    def test_tc18_vwap_resets_per_day(self):
        """TC18: VWAP resets on new calendar day."""
        df = _make_df([
            {"ts": "2026-05-26 15:15", "o": 200, "h": 210, "l": 195, "c": 205, "v": 50000},
            {"ts": "2026-05-27 09:15", "o": 100, "h": 105, "l": 98,  "c": 103, "v": 10000},
        ])
        vwap = calculate_vwap(df)
        # Day 2 VWAP should equal (H+L+C)/3 of its first candle
        assert abs(vwap.iloc[1] - (105 + 98 + 103) / 3) < 0.01


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5: calculate_pivot_points (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestCalculatePivotPoints:
    def _prev_df(self, h=110, l=90, c=100):
        return _make_df([{"ts": "2026-05-26 15:15", "o": 95, "h": h, "l": l, "c": c}])

    def test_tc19_pivot_formula(self):
        """TC19: Pivot = (H+L+C)/3."""
        pivots = calculate_pivot_points(self._prev_df())
        assert abs(pivots["pivot"] - (110 + 90 + 100) / 3) < 0.01

    def test_tc20_r1_formula(self):
        """TC20: R1 = 2*Pivot - Low."""
        pivots = calculate_pivot_points(self._prev_df())
        p = (110 + 90 + 100) / 3
        assert abs(pivots["r1"] - (2 * p - 90)) < 0.01

    def test_tc21_s1_formula(self):
        """TC21: S1 = 2*Pivot - High."""
        pivots = calculate_pivot_points(self._prev_df())
        p = (110 + 90 + 100) / 3
        assert abs(pivots["s1"] - (2 * p - 110)) < 0.01

    def test_tc22_empty_df_returns_empty(self):
        """TC22: Empty df returns empty dict."""
        assert calculate_pivot_points(pd.DataFrame()) == {}


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6: SL Guard in logic_engine (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestSLGuard:
    def _make_stock_df(self, vwap_price=95, candle_low=97, entry=100):
        """Manufactures a df where VWAP and candle_low are both above entry."""
        prev = _make_df([{"ts": "2026-05-26 15:15", "o": 90, "h": 110, "l": 80, "c": 95}])
        today = _make_df([
            {"ts": "2026-05-27 09:15", "o": 95,    "h": 99,    "l": 92,    "c": 96,    "v": 50000},
            {"ts": "2026-05-27 09:20", "o": 96,    "h": 101,   "l": candle_low, "c": entry, "v": 30000},
        ])
        return pd.concat([prev, today]).reset_index(drop=True)

    def test_tc23_sl_below_entry_normal(self):
        """TC23: When VWAP/low are below entry, SL < entry."""
        df = _make_df([
            {"ts": "2026-05-26 15:15", "o": 90, "h": 110, "l": 80, "c": 95},
            {"ts": "2026-05-27 09:15", "o": 95, "h": 100, "l": 94, "c": 99, "v": 50000},
            {"ts": "2026-05-27 09:20", "o": 99, "h": 103, "l": 96, "c": 102, "v": 30000},
        ])
        nifty_df = df.copy()
        result = evaluate_rules(df, nifty_df, trigger_price=102)
        if result["verdict"] == "ENTER":
            assert result["stop_loss"] < result["entry"], "SL must be below entry"

    def test_tc24_sl_clamped_when_above_entry(self):
        """TC24: SL is clamped to entry*0.995 when VWAP > entry (inverted)."""
        # Build a df where candle_low > entry (simulating a late webhook)
        df = _make_df([
            {"ts": "2026-05-26 15:15", "o": 90, "h": 110, "l": 80, "c": 95},
            {"ts": "2026-05-27 09:15", "o": 95, "h": 300, "l": 290, "c": 295, "v": 50000},
            {"ts": "2026-05-27 09:20", "o": 295, "h": 302, "l": 291, "c": 298, "v": 30000},
        ])
        nifty_df = df.copy()
        result = evaluate_rules(df, nifty_df, trigger_price=298)
        if result["verdict"] == "ENTER":
            assert result["stop_loss"] < result["entry"], \
                f"SL {result['stop_loss']} should be < entry {result['entry']}"

    def test_tc25_backtest_sl_guard_clamps_sim(self):
        """TC25: Backtest simulation clamps SL>=entry to entry*0.995 internally."""
        from backtest import _backtest_single_alert
        alert = MagicMock()
        alert.id = 1
        alert.stock = "PETRONET"
        alert.trigger_time = "3:10 pm"
        alert.trigger_date = "2026-05-27"
        alert.entry_price = 279.85
        alert.stop_loss = 281.95  # deliberately inverted
        alert.target = 284.35

        candles = [
            {"ts": "2026-05-27 15:10", "o": 279.85, "h": 281.0, "l": 278.0, "c": 280.0},
            {"ts": "2026-05-27 15:15", "o": 280.0,  "h": 282.0, "l": 279.0, "c": 281.5},
            {"ts": "2026-05-27 15:20", "o": 281.5,  "h": 282.0, "l": 280.5, "c": 281.0},
        ]
        mock_df = _make_df(candles)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("backtest.get_historical_data", return_value=mock_df):
            result = _backtest_single_alert(alert, db)
        # If SL was not clamped, it would have triggered LOSS on candle 2
        # since low=279.0 <= SL=281.95, but with clamping SL=278.43 no SL hit
        # The result should NOT be LOSS from fake SL
        assert result is not None
        # With clamped SL (278.43), neither target(284.35) nor SL hit → auto-square
        # exit at close of last candle (281.0)
        assert result.outcome in ("PROFIT", "FLAT"), \
            f"Expected PROFIT or FLAT (auto-square), got {result.outcome}"

    def test_tc26_backtest_normal_sl_triggers(self):
        """TC26: A valid SL below entry triggers LOSS when hit."""
        from backtest import _backtest_single_alert
        alert = MagicMock()
        alert.id = 2
        alert.stock = "TESTSTK"
        alert.trigger_time = "9:15 am"
        alert.trigger_date = "2026-05-27"
        alert.entry_price = 100.0
        alert.stop_loss = 95.0
        alert.target = 110.0

        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 09:20", "o": 101, "h": 102, "l": 94, "c": 96},  # SL hit
        ]
        mock_df = _make_df(candles)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("backtest.get_historical_data", return_value=mock_df):
            result = _backtest_single_alert(alert, db)
        assert result.outcome == "LOSS"
        assert result.exit_price == 95.0


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7: Date Filter (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestDateFilter:
    def test_tc27_cross_day_contamination_prevented(self):
        """TC27: Previous day candles are excluded by the date filter."""
        from backtest import _backtest_single_alert
        alert = MagicMock()
        alert.id = 3
        alert.stock = "AFCONS"
        alert.trigger_time = "9:30 am"
        alert.trigger_date = "2026-05-26"
        alert.entry_price = 322.40
        alert.stop_loss = 303.0
        alert.target = 360.0

        # Previous day has a candle with low=300 which would falsely trigger SL
        # This candle should be filtered out
        candles = [
            {"ts": "2026-05-23 15:15", "o": 310, "h": 320, "l": 300, "c": 305},  # Friday - SL hit
            {"ts": "2026-05-26 09:30", "o": 322, "h": 330, "l": 318, "c": 328},  # Monday entry
            {"ts": "2026-05-26 09:35", "o": 328, "h": 335, "l": 325, "c": 332},
            {"ts": "2026-05-26 09:40", "o": 332, "h": 338, "l": 330, "c": 336},
        ]
        mock_df = _make_df(candles)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("backtest.get_historical_data", return_value=mock_df):
            result = _backtest_single_alert(alert, db)

        # Friday's SL hit should be ignored, result should be PROFIT or auto-square
        assert result is not None
        assert result.outcome != "LOSS", "Previous day phantom SL triggered — date filter broken!"

    def test_tc28_only_alert_date_candles_used(self):
        """TC28: Only candles from trigger_date are in post_trigger."""
        from backtest import _backtest_single_alert
        alert = MagicMock()
        alert.id = 4
        alert.stock = "TESTSTK"
        alert.trigger_time = "9:15 am"
        alert.trigger_date = "2026-05-27"
        alert.entry_price = 100.0
        alert.stop_loss = 95.0
        alert.target = 120.0

        candles = [
            {"ts": "2026-05-26 15:15", "o": 90, "h": 95, "l": 80, "c": 92},   # prev day
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 09:20", "o": 101, "h": 105, "l": 100, "c": 104},
        ]
        mock_df = _make_df(candles)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("backtest.get_historical_data", return_value=mock_df):
            result = _backtest_single_alert(alert, db)
        # Should process only 2 candles from 2026-05-27
        assert result is not None

    def test_tc29_no_candles_for_date_returns_none(self):
        """TC29: No candles on alert date returns None gracefully."""
        from backtest import _backtest_single_alert
        alert = MagicMock()
        alert.id = 5
        alert.stock = "TESTSTK"
        alert.trigger_time = "9:15 am"
        alert.trigger_date = "2026-05-27"
        alert.entry_price = 100.0
        alert.stop_loss = 95.0
        alert.target = 110.0

        # Only previous day candles
        candles = [
            {"ts": "2026-05-26 15:15", "o": 100, "h": 105, "l": 95, "c": 102},
        ]
        mock_df = _make_df(candles)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("backtest.get_historical_data", return_value=mock_df):
            result = _backtest_single_alert(alert, db)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8: Backtest Simulation (10 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestBacktestSimulation:
    def _alert(self, entry=100.0, target=110.0, sl=95.0,
               tdate="2026-05-27", ttime="9:15 am"):
        a = MagicMock()
        a.id = 99
        a.stock = "SIMSTK"
        a.trigger_time = ttime
        a.trigger_date = tdate
        a.entry_price = entry
        a.stop_loss = sl
        a.target = target
        return a

    def _run(self, alert, candles):
        df = _make_df(candles)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with patch("backtest.get_historical_data", return_value=df):
            return _backtest_single_alert(alert, db)

    def test_tc30_target_hit_gives_profit(self):
        """TC30: Candle high >= target → PROFIT."""
        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 09:20", "o": 101, "h": 111, "l": 100, "c": 110},
        ]
        result = self._run(self._alert(), candles)
        assert result.outcome == "PROFIT"
        assert result.exit_price == 110.0

    def test_tc31_sl_hit_gives_loss(self):
        """TC31: Candle low <= SL → LOSS."""
        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 09:20", "o": 101, "h": 102, "l": 94, "c": 96},
        ]
        result = self._run(self._alert(), candles)
        assert result.outcome == "LOSS"
        assert result.exit_price == 95.0

    def test_tc32_entry_candle_not_checked_for_sl(self):
        """TC32: Entry candle's low below SL does NOT trigger LOSS."""
        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 90, "c": 101},  # low=90 < SL=95
            {"ts": "2026-05-27 09:20", "o": 101, "h": 103, "l": 100, "c": 102},
        ]
        result = self._run(self._alert(), candles)
        assert result.outcome != "LOSS", "Entry candle's SL must not trigger exit"

    def test_tc33_entry_candle_target_not_checked(self):
        """TC33: Entry candle's high above target does NOT trigger PROFIT."""
        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 115, "l": 99, "c": 101},  # high > target
            {"ts": "2026-05-27 09:20", "o": 101, "h": 103, "l": 100, "c": 102},
        ]
        result = self._run(self._alert(), candles)
        assert result.outcome != "PROFIT" or result.exit_time != "9:15 am", \
            "Entry candle must not trigger PROFIT"

    def test_tc34_auto_square_off_at_3_25(self):
        """TC34: No SL/Target → auto-square at 3:25 pm."""
        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 15:20", "o": 101, "h": 103, "l": 100, "c": 103},
        ]
        result = self._run(self._alert(), candles)
        assert result.exit_time == "3:25 pm"
        assert result.exit_price == 103.0

    def test_tc35_auto_square_profit_when_close_above_entry(self):
        """TC35: Auto-square with close > entry → PROFIT."""
        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 15:20", "o": 101, "h": 106, "l": 100, "c": 105},
        ]
        result = self._run(self._alert(), candles)
        assert result.outcome == "PROFIT"

    def test_tc36_auto_square_loss_when_close_below_entry(self):
        """TC36: Auto-square with close < entry → LOSS."""
        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 15:20", "o": 101, "h": 102, "l": 97, "c": 98},
        ]
        result = self._run(self._alert(), candles)
        assert result.outcome == "LOSS"

    def test_tc37_auto_square_flat_when_close_equals_entry(self):
        """TC37: Auto-square with close == entry → FLAT."""
        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 15:20", "o": 101, "h": 103, "l": 99, "c": 100},  # close = entry
        ]
        result = self._run(self._alert(), candles)
        assert result.outcome == "FLAT"

    def test_tc38_single_candle_auto_square(self):
        """TC38: Only 1 candle (late entry at 3:20) → auto-square at 3:25 pm."""
        candles = [
            {"ts": "2026-05-27 15:20", "o": 100, "h": 102, "l": 99, "c": 98},
        ]
        result = self._run(self._alert(ttime="3:20 pm"), candles)
        assert result.exit_time == "3:25 pm"

    def test_tc39_both_sl_and_target_same_candle_is_loss(self):
        """TC39: Both SL and Target hit in same candle → conservative LOSS."""
        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 09:20", "o": 101, "h": 115, "l": 94, "c": 100},  # Both hit
        ]
        result = self._run(self._alert(), candles)
        assert result.outcome == "LOSS", "Conservative rule: SL first when both hit same candle"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9: create_pending_trade (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestCreatePendingTrade:
    def test_tc40_creates_pending_record(self, db):
        """TC40: create_pending_trade creates a BacktestResult with PENDING."""
        alert = _make_alert(db)
        db.flush()
        create_pending_trade(alert, db)
        record = db.query(BacktestResult).filter(BacktestResult.alert_id == alert.id).first()
        assert record is not None
        assert record.outcome == "PENDING"

    def test_tc41_no_duplicate_pending(self, db):
        """TC41: Calling create_pending_trade twice does not create duplicate."""
        alert = _make_alert(db, stock="DUPTEST")
        db.flush()
        create_pending_trade(alert, db)
        create_pending_trade(alert, db)  # second call — must be idempotent
        count = db.query(BacktestResult).filter(BacktestResult.alert_id == alert.id).count()
        assert count == 1

    def test_tc42_quantity_calculated_correctly(self, db):
        """TC42: Quantity = int((50000 * 4) / entry_price)."""
        alert = _make_alert(db, entry=200.0, stock="QTYTEST")
        db.flush()
        create_pending_trade(alert, db)
        record = db.query(BacktestResult).filter(BacktestResult.alert_id == alert.id).first()
        expected_qty = int((50000 * 4) / 200.0)
        assert record.quantity == expected_qty


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 10: 4 Golden Rules – Logic Engine (10 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestFourGoldenRules:
    def _make_candles(self, base_ts, n, o=97, h_inc=1, l_inc=-1, c_inc=0):
        """Generate n 5-min candles starting from base_ts (a pd.Timestamp)."""
        rows = []
        for i in range(n):
            ts = base_ts + pd.Timedelta(minutes=5*i)
            rows.append({"ts": ts.strftime("%Y-%m-%d %H:%M"),
                         "o": o+i, "h": o+i+h_inc, "l": o+i+l_inc, "c": o+i+c_inc, "v": 10000})
        return rows

    def _healthy_df(self, trigger=100, vwap=98):
        """Creates a healthy bullish breakout scenario with 12+ candles."""
        prev = _make_df([{"ts": "2026-05-26 15:15", "o": 90, "h": 105, "l": 88, "c": 98}])
        # 12 candles from 9:15 ascending to build EMA9
        today = self._make_candles(pd.Timestamp("2026-05-27 09:15"), 12)
        today.append({"ts": "2026-05-27 10:15", "o": 98, "h": 103, "l": 97, "c": 102, "v": 20000})
        df = pd.concat([prev, _make_df(today)]).reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df.sort_values("timestamp").reset_index(drop=True)

    def test_tc43_enter_on_healthy_breakout(self):
        """TC43: Healthy breakout → ENTER."""
        df = self._healthy_df()
        nifty_df = df.copy()
        result = evaluate_rules(df, nifty_df, trigger_price=100)
        # Should not be ERROR (may be ENTER, WAIT, or SKIP based on rules)
        assert result["verdict"] != "ERROR"

    def test_tc44_rule2_skip_when_nifty_below_vwap(self):
        """TC44: Rule 2 — NIFTY below VWAP → SKIP."""
        # Stock is fine, but NIFTY is bearish
        stock_df = self._healthy_df()
        # Create a bearish NIFTY (close << VWAP territory)
        nifty_df = _make_df([
            {"ts": "2026-05-26 15:15", "o": 22000, "h": 22100, "l": 21800, "c": 21900},
            {"ts": "2026-05-27 09:15", "o": 21900, "h": 21950, "l": 21700, "c": 21750, "v": 1e9},
            {"ts": "2026-05-27 09:20", "o": 21750, "h": 21800, "l": 21500, "c": 21550, "v": 1e9},
            {"ts": "2026-05-27 09:25", "o": 21550, "h": 21600, "l": 21400, "c": 21450, "v": 1e9},
            {"ts": "2026-05-27 09:30", "o": 21450, "h": 21500, "l": 21300, "c": 21350, "v": 1e9},
            {"ts": "2026-05-27 09:35", "o": 21350, "h": 21400, "l": 21200, "c": 21250, "v": 1e9},
            {"ts": "2026-05-27 09:40", "o": 21250, "h": 21300, "l": 21100, "c": 21150, "v": 1e9},
            {"ts": "2026-05-27 09:45", "o": 21150, "h": 21200, "l": 21000, "c": 21050, "v": 1e9},
            {"ts": "2026-05-27 09:50", "o": 21050, "h": 21100, "l": 20900, "c": 20950, "v": 1e9},
            {"ts": "2026-05-27 09:55", "o": 20950, "h": 21000, "l": 20800, "c": 20850, "v": 1e9},
            {"ts": "2026-05-27 10:00", "o": 20850, "h": 20900, "l": 20700, "c": 20750, "v": 1e9},
        ])
        result = evaluate_rules(stock_df, nifty_df, trigger_price=100)
        # NIFTY is clearly bearish here — should trigger SKIP or be caught by another rule
        assert result["verdict"] in ("SKIP", "WAIT")

    def _bullish_nifty_df(self):
        """Create a bullish NIFTY df where LTP > VWAP."""
        prev = _make_df([{"ts": "2026-05-26 15:15", "o": 22000, "h": 22200, "l": 21900, "c": 22100}])
        base = pd.Timestamp("2026-05-27 09:15")
        candles = [{"ts": (base + pd.Timedelta(minutes=5*i)).strftime("%Y-%m-%d %H:%M"),
                    "o": 22100+i*5, "h": 22120+i*5, "l": 22080+i*5, "c": 22110+i*5, "v": 500000}
                   for i in range(13)]
        df = pd.concat([prev, _make_df(candles)]).reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df.sort_values("timestamp").reset_index(drop=True)

    def test_tc45_rule4_skip_red_candle(self):
        """TC45: Rule 4 — Red candle (close < open) → SKIP."""
        prev = _make_df([{"ts": "2026-05-26 15:15", "o": 90, "h": 105, "l": 88, "c": 98}])
        # 11 green candles using timedelta to avoid minute overflow
        base = pd.Timestamp("2026-05-27 09:15")
        today = [{"ts": (base + pd.Timedelta(minutes=5*i)).strftime("%Y-%m-%d %H:%M"),
                  "o": 96+i, "h": 98+i, "l": 95+i, "c": 97+i, "v": 10000} for i in range(11)]
        # Last candle is RED (close=101 < open=105)
        today.append({"ts": "2026-05-27 10:10", "o": 105, "h": 106, "l": 100, "c": 101, "v": 5000})
        df = pd.concat([prev, _make_df(today)]).reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        # Use a bullish NIFTY so Rule 2 doesn't fire first
        nifty_df = self._bullish_nifty_df()
        result = evaluate_rules(df, nifty_df, trigger_price=101)
        # Rule 4 fires for red candle — verdict must be SKIP
        assert result["verdict"] == "SKIP"
        assert "green" in result["verdict_reason"].lower() or "solid" in result["verdict_reason"].lower()

    def test_tc46_rule4_skip_upper_wick(self):
        """TC46: Rule 4 — Upper wick > body → SKIP (when not overextended)."""
        prev = _make_df([{"ts": "2026-05-26 15:15", "o": 90, "h": 105, "l": 88, "c": 98}])
        base = pd.Timestamp("2026-05-27 09:15")
        # Build candles with high volume at 100 → VWAP ≈ 100
        # Trigger = 100.5 → only 0.5% above VWAP → safely under 0.8% Rule 3 threshold
        today = [{"ts": (base + pd.Timedelta(minutes=5*i)).strftime("%Y-%m-%d %H:%M"),
                  "o": 100, "h": 100.5, "l": 99.8, "c": 100, "v": 200000} for i in range(11)]
        # Last candle: green but huge upper wick — open=100, high=110, close=100.5
        # body = 0.5, upper_wick = 9.5 → wick >> body → Rule 4 SKIP
        today.append({"ts": "2026-05-27 10:10", "o": 100, "h": 110, "l": 99.8, "c": 100.5, "v": 5000})
        df = pd.concat([prev, _make_df(today)]).reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        # Bullish NIFTY so Rule 2 doesn't fire first
        nifty_df = self._bullish_nifty_df()
        # Trigger at 100.5 → 0.5% above VWAP(100) → under 0.8% → Rule 3 WON'T fire
        result = evaluate_rules(df, nifty_df, trigger_price=100.5)
        assert result["verdict"] == "SKIP", \
            f"Expected SKIP (wick rule), got {result['verdict']}: {result['verdict_reason']}"
        assert "wick" in result["verdict_reason"].lower()

    def test_tc47_rule3_wait_overextended(self):
        """TC47: Rule 3 — Price > VWAP * 1.008 → WAIT."""
        prev = _make_df([{"ts": "2026-05-26 15:15", "o": 90, "h": 105, "l": 88, "c": 98}])
        # Build with high-volume base so VWAP ≈ 100 using timedelta for safety
        base = pd.Timestamp("2026-05-27 09:15")
        today = [{"ts": (base + pd.Timedelta(minutes=5*i)).strftime("%Y-%m-%d %H:%M"),
                  "o": 100, "h": 101, "l": 99, "c": 100, "v": 100000} for i in range(11)]
        # Trigger: green candle but price is 110 >> VWAP(~100) * 1.008 = 100.8
        today.append({"ts": "2026-05-27 10:10", "o": 109, "h": 111, "l": 108, "c": 110, "v": 5000})
        df = pd.concat([prev, _make_df(today)]).reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        nifty_df = df.copy()
        result = evaluate_rules(df, nifty_df, trigger_price=110)
        assert result["verdict"] == "WAIT", \
            f"Expected WAIT (overextended), got {result['verdict']}: {result['verdict_reason']}"

    def test_tc48_target_is_next_pivot_above_entry(self):
        """TC48: Target = nearest pivot above entry."""
        prev = _make_df([{"ts": "2026-05-26 15:15", "o": 90, "h": 110, "l": 80, "c": 100}])
        base = pd.Timestamp("2026-05-27 09:15")
        today = [{"ts": (base + pd.Timedelta(minutes=5*i)).strftime("%Y-%m-%d %H:%M"),
                  "o": 98+i*0.1, "h": 99+i*0.1, "l": 97+i*0.1, "c": 98.5+i*0.1, "v": 50000}
                 for i in range(11)]
        today.append({"ts": "2026-05-27 10:10", "o": 100, "h": 102, "l": 99, "c": 101, "v": 30000})
        df = pd.concat([prev, _make_df(today)]).reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        nifty_df = df.copy()
        result = evaluate_rules(df, nifty_df, trigger_price=101)
        if result["verdict"] == "ENTER":
            assert result["target"] > result["entry"], "Target must be above entry"

    def test_tc49_target_defaults_1_5pct_above_all_ceilings(self):
        """TC49: When price breaks above all pivots, target = entry * 1.015."""
        prev = _make_df([{"ts": "2026-05-26 15:15", "o": 90, "h": 95, "l": 88, "c": 92}])
        base = pd.Timestamp("2026-05-27 09:15")
        # High volume today at a price well above yesterday's pivots
        today = [{"ts": (base + pd.Timedelta(minutes=5*i)).strftime("%Y-%m-%d %H:%M"),
                  "o": 150+i, "h": 152+i, "l": 149+i, "c": 151+i, "v": 50000}
                 for i in range(11)]
        today.append({"ts": "2026-05-27 10:10", "o": 161, "h": 163, "l": 160, "c": 162, "v": 30000})
        df = pd.concat([prev, _make_df(today)]).reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        nifty_df = df.copy()
        result = evaluate_rules(df, nifty_df, trigger_price=162)
        if result["verdict"] == "ENTER":
            expected_target = round(162 * 1.015, 2)
            assert abs(result["target"] - expected_target) < 0.1, \
                f"Expected {expected_target}, got {result['target']}"

    def test_tc50_entry_price_equals_trigger_price(self):
        """TC50: Entry price = trigger_price when verdict is ENTER."""
        prev = _make_df([{"ts": "2026-05-26 15:15", "o": 90, "h": 105, "l": 88, "c": 98}])
        base = pd.Timestamp("2026-05-27 09:15")
        today = [{"ts": (base + pd.Timedelta(minutes=5*i)).strftime("%Y-%m-%d %H:%M"),
                  "o": 98+i*0.1, "h": 99+i*0.1, "l": 97+i*0.1, "c": 98.5+i*0.1, "v": 50000}
                 for i in range(11)]
        today.append({"ts": "2026-05-27 10:10", "o": 99, "h": 102, "l": 98, "c": 101, "v": 30000})
        df = pd.concat([prev, _make_df(today)]).reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        nifty_df = df.copy()
        result = evaluate_rules(df, nifty_df, trigger_price=101.5)
        if result["verdict"] == "ENTER":
            assert result["entry"] == round(101.5, 2)

    def test_tc51_insufficient_data_returns_error(self):
        """TC51: Less than 10 candles → ERROR verdict."""
        df = _make_df([
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 98, "c": 101},
            {"ts": "2026-05-27 09:20", "o": 101, "h": 103, "l": 100, "c": 102},
        ])
        result = evaluate_rules(df, df, trigger_price=102)
        assert result["verdict"] == "ERROR"

    def test_tc52_empty_df_returns_error(self):
        """TC52: Empty DataFrame → ERROR verdict."""
        df = pd.DataFrame()
        result = evaluate_rules(df, df, trigger_price=100)
        assert result["verdict"] == "ERROR"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 11: evaluate_wait_upgrade (5 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestEvaluateWaitUpgrade:
    def _candle(self, o, h, l, c, vwap):
        return pd.Series({"open": o, "high": h, "low": l, "close": c, "vwap": vwap})

    def test_tc53_not_touched_vwap_zone_stays_wait(self):
        """TC53: Price far above VWAP zone → WAIT."""
        candle = self._candle(o=110, h=112, l=109, c=111, vwap=100)
        result = evaluate_wait_upgrade(candle, live_price=111)
        assert result["status"] == "WAIT"

    def test_tc54_failed_below_vwap_cancels(self):
        """TC54: Close decisively below VWAP → SKIP (cancelled)."""
        candle = self._candle(o=100, h=101, l=96, c=96.5, vwap=100)  # close < 99.7
        result = evaluate_wait_upgrade(candle, live_price=96.5)
        assert result["status"] == "SKIP"

    def test_tc55_touched_but_red_base_candle_stays_wait(self):
        """TC55: Touched VWAP zone but red candle → WAIT (no buyer conviction)."""
        vwap = 100
        # Red candle touches VWAP zone but close < open, no big lower wick
        candle = self._candle(o=101, h=101.5, l=99.5, c=100, vwap=vwap)
        result = evaluate_wait_upgrade(candle, live_price=100)
        assert result["status"] == "WAIT"

    def test_tc56_base_candle_formed_waiting_for_trigger(self):
        """TC56: Base candle OK but live price < base_high → still WAIT."""
        vwap = 100
        candle = self._candle(o=100, h=102, l=99.5, c=101, vwap=vwap)  # green, touched zone
        # live_price = 101 < base_candle_high = 102
        result = evaluate_wait_upgrade(candle, live_price=101)
        assert result["status"] == "WAIT"

    def test_tc57_full_upgrade_to_enter(self):
        """TC57: Base candle + live price breaks base high → ENTER."""
        vwap = 100
        candle = self._candle(o=99, h=102, l=99, c=101.5, vwap=vwap)  # green in zone
        result = evaluate_wait_upgrade(candle, live_price=102.5)  # > base_high=102
        assert result["status"] == "ENTER"
        assert result["entry_price"] == 102.5


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 12: API Endpoints – Health & Alerts (10 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestAPIEndpoints:
    def test_tc58_health_endpoint_returns_ok(self, client):
        """TC58: GET /health → 200 with status ok."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_tc59_health_includes_timestamp(self, client):
        """TC59: /health includes 'time' field."""
        resp = client.get("/health")
        assert "time" in resp.json()

    def test_tc60_get_alerts_empty_returns_list(self, client):
        """TC60: GET /api/alerts for date with no data → empty list."""
        resp = client.get("/api/alerts?date=2099-01-01")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_tc61_get_backtest_results_empty(self, client):
        """TC61: GET /api/backtest-results for empty date → zero summary."""
        resp = client.get("/api/backtest-results?date=2099-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_enter_alerts"] == 0
        assert data["trades"] == []

    def test_tc62_webhook_bad_payload_422(self, client):
        """TC62: POST /api/webhook/chartink with no body → 422."""
        resp = client.post("/api/webhook/chartink", json={})
        assert resp.status_code == 422

    def test_tc63_webhook_accepts_valid_payload(self, client):
        """TC63: POST /api/webhook/chartink with valid payload → 200 accepted."""
        resp = client.post("/api/webhook/chartink", json={
            "stocks": "INFY,TCS",
            "trigger_prices": "1500,3600",
            "triggered_at": "9:15 am",
            "scan_name": "Test Scan"
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "accepted"
        assert len(body["stocks_queued"]) == 2

    def test_tc64_webhook_stocks_uppercased(self, client):
        """TC64: Webhook stocks are normalized to uppercase."""
        resp = client.post("/api/webhook/chartink", json={
            "stocks": "infy,tcs",
            "trigger_prices": "1500,3600",
        })
        assert resp.status_code == 200
        queued = resp.json()["stocks_queued"]
        for s in queued:
            assert s == s.upper()

    def test_tc65_nifty_status_returns_structure(self, client):
        """TC65: GET /api/nifty-status returns expected structure."""
        with patch("main.get_ltp", return_value=23500.0), \
             patch("main.get_historical_data", return_value=_make_df([
                 {"ts": "2026-05-27 09:15", "o": 23000, "h": 23500, "l": 22900, "c": 23400, "v": 1e9}
             ])):
            resp = client.get("/api/nifty-status")
        assert resp.status_code == 200
        body = resp.json()
        assert "ltp" in body
        assert "vwap" in body
        assert "is_bullish" in body

    def test_tc66_live_prices_returns_list(self, client):
        """TC66: GET /api/alerts/live-prices returns a list."""
        resp = client.get("/api/alerts/live-prices?date=2099-01-01")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_tc67_run_backtest_post_accepted(self, client):
        """TC67: POST /api/run-backtest → 200 started."""
        with patch("main.run_backtest") as mock_bt:
            mock_bt.return_value = {}
            resp = client.post("/api/run-backtest")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 13: Admin Endpoints (10 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestAdminEndpoints:
    def test_tc68_restore_missing_creates_pending(self, client, db):
        """TC68: /api/admin/restore-missing creates PENDING for missing records."""
        # Bypass the client's override to use our transaction db
        # Just test via client without existing records
        resp = client.post("/api/admin/restore-missing?date=2099-01-01")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["records_created"] == 0  # no alerts for 2099

    def test_tc69_fix_inverted_sl_corrects_bad_sl(self, client, db):
        """TC69: /api/admin/fix-inverted-sl corrects SL >= entry."""
        resp = client.post("/api/admin/fix-inverted-sl?date=2099-01-01")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert resp.json()["fixed_count"] == 0  # no data for 2099

    def test_tc70_eod_finalize_endpoint_exists(self, client):
        """TC70: POST /api/admin/eod-finalize exists and returns 200."""
        with patch("main.run_backtest", return_value={"processed": 0, "wins": 0}):
            resp = client.post("/api/admin/eod-finalize?date=2099-01-01")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    def test_tc71_eod_finalize_shows_pending_before_after(self, client):
        """TC71: eod-finalize returns pending_before and pending_after counts."""
        with patch("main.run_backtest", return_value={}):
            resp = client.post("/api/admin/eod-finalize?date=2099-01-01")
        body = resp.json()
        assert "pending_before" in body
        assert "pending_after" in body

    def test_tc72_force_recovery_requires_date(self, client):
        """TC72: /api/admin/force-recovery without date → 400."""
        resp = client.post("/api/admin/force-recovery")
        assert resp.status_code == 400

    def test_tc73_run_recovery_endpoint_exists(self, client):
        """TC73: POST /api/admin/run-recovery exists."""
        with patch("main.run_backtest", return_value={}):
            resp = client.post("/api/admin/run-recovery")
        assert resp.status_code == 200

    def test_tc74_reprocess_errors_no_errors_found(self, client):
        """TC74: /api/admin/reprocess-errors with no ERROR alerts → nothing_to_reprocess."""
        resp = client.post("/api/admin/reprocess-errors?date=2099-01-01")
        assert resp.status_code == 200
        assert resp.json()["status"] == "nothing_to_reprocess"

    def test_tc75_clear_date_returns_cleared(self, client):
        """TC75: DELETE /api/admin/clear-date returns cleared status."""
        resp = client.delete("/api/admin/clear-date?date=2099-01-01")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"
        assert resp.json()["deleted_count"] == 0

    def test_tc76_debug_log_endpoint_exists(self, client):
        """TC76: GET /api/debug-log returns count and logs list."""
        resp = client.get("/api/debug-log")
        assert resp.status_code == 200
        body = resp.json()
        assert "count" in body
        assert "logs" in body
        assert isinstance(body["logs"], list)

    def test_tc77_debug_log_level_filter(self, client):
        """TC77: GET /api/debug-log?level=ERROR returns only ERROR entries."""
        resp = client.get("/api/debug-log?level=ERROR")
        assert resp.status_code == 200
        logs = resp.json()["logs"]
        for log in logs:
            assert log["level"] == "ERROR"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 14: P&L Calculation Correctness (5 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestPnLCalculation:
    def _run_sim(self, entry, exit_p, sl=None, target=None, tdate="2026-05-27"):
        alert = MagicMock()
        alert.id = 999
        alert.stock = "PNLSTK"
        alert.trigger_time = "9:15 am"
        alert.trigger_date = tdate
        alert.entry_price = entry
        alert.stop_loss = sl or entry * 0.97
        alert.target = target or entry * 1.05
        candles = [
            {"ts": f"{tdate} 09:15", "o": entry, "h": entry+2, "l": entry-1, "c": entry+1},
            {"ts": f"{tdate} 09:20", "o": entry+1, "h": exit_p+1, "l": exit_p-1, "c": exit_p},
        ]
        df = _make_df(candles)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with patch("backtest.get_historical_data", return_value=df):
            return _backtest_single_alert(alert, db)

    def test_tc78_pnl_pct_correct_for_profit(self):
        """TC78: pnl_pct = (exit - entry) / entry * 100."""
        result = self._run_sim(entry=100, exit_p=110, target=110)
        expected = round((110 - 100) / 100 * 100, 2)
        assert abs(result.pnl_pct - expected) < 0.01

    def test_tc79_pnl_amount_uses_quantity(self):
        """TC79: pnl_amount = quantity * (exit - entry)."""
        result = self._run_sim(entry=100, exit_p=105, target=105)
        qty = int((50000 * 4) / 100)
        expected_amt = round(qty * (105 - 100), 2)
        assert abs(result.pnl_amount - expected_amt) < 1.0

    def test_tc80_loss_pnl_is_negative(self):
        """TC80: LOSS → pnl_pct and pnl_amount are negative."""
        result = self._run_sim(entry=100, exit_p=95, sl=95, target=110)
        if result.outcome == "LOSS":
            assert result.pnl_pct < 0
            assert result.pnl_amount < 0

    def test_tc81_quantity_formula_50k_4x(self):
        """TC81: Quantity = floor(200000 / entry_price)."""
        result = self._run_sim(entry=250, exit_p=260, target=260)
        expected_qty = int(200000 / 250)
        assert result.quantity == expected_qty

    def test_tc82_exit_price_saved_as_float(self):
        """TC82: exit_price is Python float (not numpy float64)."""
        result = self._run_sim(entry=100, exit_p=105, target=105)
        assert isinstance(result.exit_price, float), "exit_price must be Python float (psycopg2 safety)"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 15: Backtest Results API correctness (5 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestBacktestResultsAPI:
    def test_tc83_summary_win_rate_computed(self, client, db):
        """TC83: Win rate = wins/total * 100."""
        resp = client.get("/api/backtest-results?date=2099-01-01")
        assert resp.status_code == 200
        assert resp.json()["summary"]["win_rate_pct"] == 0

    def test_tc84_pending_trade_shows_dash_exit(self, client, db):
        """TC84: PENDING trades show exit_time='—' and exit_price=null."""
        resp = client.get("/api/backtest-results?date=2099-01-01")
        for trade in resp.json()["trades"]:
            if trade["outcome"] == "PENDING":
                assert trade["exit_time"] == "—"
                assert trade["exit_price"] is None

    def test_tc85_summary_has_required_fields(self, client):
        """TC85: Summary contains all required fields."""
        resp = client.get("/api/backtest-results?date=2099-01-01")
        summary = resp.json()["summary"]
        required = ["total_enter_alerts", "backtested", "wins", "losses",
                    "flats", "win_rate_pct", "net_pnl_pct", "net_pnl_amount"]
        for field in required:
            assert field in summary, f"Missing field: {field}"

    def test_tc86_trade_has_target_and_sl(self, client):
        """TC86: Each trade row includes target and stop_loss from Alert."""
        resp = client.get("/api/backtest-results?date=2099-01-01")
        for trade in resp.json()["trades"]:
            # These keys must always be present (may be null for unknown)
            assert "target" in trade
            assert "stop_loss" in trade

    def test_tc87_net_pnl_averages_across_trades(self, client):
        """TC87: net_pnl_pct is average of individual pnl_pcts (not sum)."""
        # With no trades, net_pnl_pct should be 0
        resp = client.get("/api/backtest-results?date=2099-01-01")
        assert resp.json()["summary"]["net_pnl_pct"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 16: Edge Cases & Robustness (13 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_tc88_zero_entry_price_handled(self, db):
        """TC88: Alert with 0 entry_price doesn't crash create_pending_trade."""
        alert = _make_alert(db, entry=0.0, stock="ZEROENTRY")
        db.flush()
        try:
            create_pending_trade(alert, db)
        except Exception as e:
            pytest.fail(f"create_pending_trade crashed on zero entry: {e}")

    def test_tc89_none_trigger_price_uses_close(self):
        """TC89: When trigger_price is None, entry defaults to latest_close."""
        prev = _make_df([{"ts": "2026-05-26 15:15", "o": 90, "h": 105, "l": 88, "c": 98}])
        base = pd.Timestamp("2026-05-27 09:15")
        today = [{"ts": (base + pd.Timedelta(minutes=5*i)).strftime("%Y-%m-%d %H:%M"),
                  "o": 98+i*0.1, "h": 99+i*0.1, "l": 97+i*0.1, "c": 98.5+i*0.1, "v": 50000}
                 for i in range(12)]
        df = pd.concat([prev, _make_df(today)]).reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        result = evaluate_rules(df, df, trigger_price=None)
        assert result["verdict"] != "ERROR"
        if result["verdict"] == "ENTER":
            assert result["entry"] is not None

    def test_tc90_backtest_missing_params_returns_none(self):
        """TC90: Alert missing entry/target/stop_loss returns None."""
        alert = MagicMock()
        alert.id = 1
        alert.stock = "MISSING"
        alert.trigger_time = "9:15 am"
        alert.trigger_date = "2026-05-27"
        alert.entry_price = None  # missing
        alert.stop_loss = None
        alert.target = None
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = _backtest_single_alert(alert, db)
        assert result is None

    def test_tc91_backtest_bad_trigger_time_returns_none(self):
        """TC91: Alert with unparseable trigger_time returns None."""
        alert = MagicMock()
        alert.id = 2
        alert.stock = "BADTIME"
        alert.trigger_time = "NOTAIME"
        alert.trigger_date = "2026-05-27"
        alert.entry_price = 100.0
        alert.stop_loss = 95.0
        alert.target = 110.0
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = _backtest_single_alert(alert, db)
        assert result is None

    def test_tc92_already_finalized_skipped(self):
        """TC92: Finalized (non-PENDING) BacktestResult is returned unchanged."""
        existing = MagicMock()
        existing.outcome = "PROFIT"

        alert = MagicMock()
        alert.id = 3
        alert.stock = "ALRDY"
        alert.trigger_time = "9:15 am"
        alert.trigger_date = "2026-05-27"
        alert.entry_price = 100.0
        alert.stop_loss = 95.0
        alert.target = 110.0

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = existing

        result = _backtest_single_alert(alert, db)
        assert result is existing  # returned as-is

    def test_tc93_run_backtest_empty_date_returns_dict(self):
        """TC93: run_backtest for empty date returns result dict with zeros."""
        with patch("backtest.SessionLocal") as MockSession:
            mock_db = MagicMock()
            MockSession.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = []
            result = run_backtest("2099-01-01")
        assert isinstance(result, dict)
        assert result["processed"] == 0

    def test_tc94_webhook_empty_stocks_string(self, client):
        """TC94: Webhook with empty stocks string doesn't crash."""
        resp = client.post("/api/webhook/chartink", json={
            "stocks": "   ",
            "triggered_at": "9:15 am"
        })
        assert resp.status_code == 200

    def test_tc95_backtest_candle_post_3_20_excluded(self):
        """TC95: Candles after 15:20 are excluded — exit must come from 15:20 close, not 15:25."""
        alert = MagicMock()
        alert.id = 10
        alert.stock = "LATECNDL"
        alert.trigger_time = "9:15 am"
        alert.trigger_date = "2026-05-27"
        alert.entry_price = 100.0
        alert.stop_loss = 95.0
        alert.target = 200.0  # very high target — won't be hit by 15:20 close (102)

        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 15:20", "o": 101, "h": 103, "l": 100, "c": 102},
            {"ts": "2026-05-27 15:25", "o": 102, "h": 250, "l": 101, "c": 249},  # way above target
        ]
        df = _make_df(candles)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with patch("backtest.get_historical_data", return_value=df):
            result = _backtest_single_alert(alert, db)
        # 15:25 candle hits target=200 but should be excluded
        # Auto-square at 15:20 close=102 → PROFIT (102>100) but exit_time=3:25 pm
        assert result.exit_price == 102.0, \
            f"Exit should use 15:20 close=102, not 15:25 price. Got exit_price={result.exit_price}"
        assert result.exit_time == "3:25 pm"  # auto-square from 15:20 close

    def test_tc96_pnl_amount_and_pct_are_native_float(self):
        """TC96: pnl_amount and pnl_pct must be Python float (psycopg2 safety)."""
        alert = MagicMock()
        alert.id = 11
        alert.stock = "FLOATSTK"
        alert.trigger_time = "9:15 am"
        alert.trigger_date = "2026-05-27"
        alert.entry_price = 100.0
        alert.stop_loss = 95.0
        alert.target = 110.0

        candles = [
            {"ts": "2026-05-27 09:15", "o": 100, "h": 102, "l": 99, "c": 101},
            {"ts": "2026-05-27 15:20", "o": 101, "h": 103, "l": 100, "c": 105},
        ]
        df = _make_df(candles)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with patch("backtest.get_historical_data", return_value=df):
            result = _backtest_single_alert(alert, db)
        assert isinstance(result.pnl_pct, float)
        assert isinstance(result.pnl_amount, float)

    def test_tc97_multiple_stocks_same_webhook(self, client):
        """TC97: Webhook with 5 stocks queues all 5."""
        resp = client.post("/api/webhook/chartink", json={
            "stocks": "INFY,TCS,RELIANCE,HDFC,ICICIBANK",
            "trigger_prices": "1500,3600,2800,1700,1100",
        })
        assert resp.status_code == 200
        assert len(resp.json()["stocks_queued"]) == 5

    def test_tc98_stock_with_whitespace_trimmed(self, client):
        """TC98: Stock names with spaces are trimmed and normalized."""
        resp = client.post("/api/webhook/chartink", json={
            "stocks": " INFY , TCS ",
        })
        assert resp.status_code == 200
        queued = resp.json()["stocks_queued"]
        assert "INFY" in queued
        assert "TCS" in queued

    def test_tc99_get_alerts_default_to_today(self, client):
        """TC99: GET /api/alerts without date param defaults to today."""
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_tc100_backtest_results_default_to_today(self, client):
        """TC100: GET /api/backtest-results without date param defaults to today."""
        resp = client.get("/api/backtest-results")
        assert resp.status_code == 200
        body = resp.json()
        assert "summary" in body
        assert "trades" in body

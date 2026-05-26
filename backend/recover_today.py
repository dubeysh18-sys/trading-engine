"""
recover_today.py — Emergency alert recovery script

Replays today's missed Chartink alerts using historical Upstox 5-min data.
Run this from the backend/ folder with the venv activated.

Usage:
    python recover_today.py
"""

import os, sys, logging
from datetime import datetime, time
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("recovery")

# ─── MISSED ALERTS FROM TODAY (edit these from WhatsApp if needed) ─────────────
# Format: (stock_symbol, trigger_time_str, scan_name)
# Trigger prices will be auto-fetched from the 5-min candle at that time.
MISSED_ALERTS = [
    ("ZYDUSLIFE",   "11:05 am", "Shubham top 10"),
    ("BHEL",        "11:15 am", "Shubham top 10"),
    ("HINDZINC",    "11:25 am", "Shubham top 10"),
    ("JSWINFRA",    "11:28 am", "Shubham top 10"),
    ("LT",          "11:40 am", "Shubham top 10"),
    ("ENRIN",       "11:55 am", "Shubham top 10"),
    ("SHYAMMETL",   "12:00 pm", "Shubham top 10"),
]

TODAY = datetime.now().strftime("%Y-%m-%d")

# ─── Setup ─────────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from database import init_db, SessionLocal, Alert
from upstox_client import get_historical_data, get_ltp
from logic_engine import evaluate_rules, calculate_vwap
from backtest import create_pending_trade, parse_trigger_time


def find_candle_at_time(df, trigger_time_str: str):
    """Find the 5-min candle closest to the trigger time."""
    t = parse_trigger_time(trigger_time_str)
    if t is None:
        return None
    df = df.copy()
    df["time_only"] = df["timestamp"].dt.time
    # Get today's candles only
    df["date"] = df["timestamp"].dt.date
    today = df["date"].max()
    today_df = df[df["date"] == today].copy()
    # Find candle at or just before trigger time
    before = today_df[today_df["time_only"] <= t]
    if before.empty:
        return None
    return before.iloc[-1]


def _f(v):
    """Convert numpy scalar to plain Python float."""
    if v is None:
        return None
    try:
        return float(v)
    except:
        return None


def recover():
    init_db()
    db = SessionLocal()
    recovered = 0
    skipped   = 0
    errors    = []

    # Pre-fetch NIFTY once
    logger.info("Fetching NIFTY 5-min data...")
    try:
        nifty_df = get_historical_data("NIFTY50", days=3)
        logger.info(f"  NIFTY data: {len(nifty_df)} candles")
    except Exception as e:
        logger.warning(f"  NIFTY fetch failed: {e} — market wind rule will use fallback")
        nifty_df = None

    for stock, trigger_time_str, scan_name in MISSED_ALERTS:
        logger.info(f"\n{'─'*50}")
        logger.info(f"Recovering: {stock} @ {trigger_time_str}")

        try:
            # Check if already saved (avoid duplicates)
            existing = db.query(Alert).filter(
                Alert.stock == stock,
                Alert.trigger_date == TODAY,
                Alert.trigger_time == trigger_time_str,
            ).first()
            if existing:
                logger.info(f"  ✓ Already in DB (verdict={existing.verdict}) — skipping")
                skipped += 1
                continue

            # Fetch stock historical data
            stock_df = get_historical_data(stock, days=3)
            logger.info(f"  Stock data: {len(stock_df)} candles")

            # Find the trigger candle at the alert time
            trigger_candle = find_candle_at_time(stock_df, trigger_time_str)
            if trigger_candle is None:
                logger.warning(f"  No candle found at {trigger_time_str} for {stock}")
                errors.append(f"{stock}: no candle at {trigger_time_str}")
                continue

            trigger_price = float(trigger_candle["close"])
            logger.info(f"  Trigger candle close (used as entry): ₹{trigger_price:.2f}")

            # Trim stock_df to only data UP TO the trigger candle (so rules see what the system would have seen then)
            trigger_ts = trigger_candle["timestamp"]
            stock_df_at_trigger = stock_df[stock_df["timestamp"] <= trigger_ts].copy()

            # Same for NIFTY
            nifty_df_at_trigger = nifty_df[nifty_df["timestamp"] <= trigger_ts].copy() if nifty_df is not None else None

            # Evaluate rules as of that moment
            result = evaluate_rules(
                stock_df = stock_df_at_trigger,
                nifty_df = nifty_df_at_trigger if nifty_df_at_trigger is not None else stock_df_at_trigger,
                trigger_price = trigger_price
            )

            logger.info(f"  Verdict: {result['verdict']} | {result['verdict_reason']}")
            if result.get("entry"):
                logger.info(f"  Entry: ₹{result['entry']:.2f}  Target: ₹{result.get('target', 0):.2f}  SL: ₹{result.get('stop_loss', 0):.2f}")

            # Save alert
            alert = Alert(
                scan_name      = scan_name,
                alert_name     = f"Alert for {scan_name}",
                stock          = stock,
                trigger_time   = trigger_time_str,
                trigger_date   = TODAY,
                trigger_price  = _f(trigger_price),
                entry_price    = _f(result["entry"]),
                target         = _f(result["target"]),
                stop_loss      = _f(result["stop_loss"]),
                vwap           = _f(result["vwap"]),
                ema9           = _f(result["ema9"]),
                pivot_r1       = _f(result["pivot_r1"]),
                pivot_r2       = _f(result["pivot_r2"]),
                pivot_s1       = _f(result["pivot_s1"]),
                upper_wick     = _f(result["upper_wick"]),
                solid_body     = _f(result["solid_body"]),
                nifty_ltp      = _f(result["nifty_ltp"]),
                nifty_vwap     = _f(result["nifty_vwap"]),
                verdict        = result["verdict"],
                verdict_reason = result["verdict_reason"],
            )
            db.add(alert)

            # If ENTER, create pending backtest record
            if alert.verdict == "ENTER":
                db.flush()
                create_pending_trade(alert, db)

            db.commit()
            logger.info(f"  ✓ Saved to database!")
            recovered += 1

        except Exception as e:
            logger.error(f"  ERROR recovering {stock}: {e}", exc_info=True)
            db.rollback()
            errors.append(f"{stock}: {e}")

    db.close()

    print("\n" + "═"*50)
    print(f"RECOVERY COMPLETE")
    print(f"  Recovered : {recovered}")
    print(f"  Skipped   : {skipped} (already in DB)")
    print(f"  Errors    : {len(errors)}")
    for e in errors:
        print(f"    ✗ {e}")
    print("═"*50)
    print("\nNow open your dashboard — all alerts should be visible.")
    print("Click 'Run Daily Backtest' to simulate today's ENTER trades.")


if __name__ == "__main__":
    recover()

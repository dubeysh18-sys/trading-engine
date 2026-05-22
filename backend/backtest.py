"""
backtest.py — End-of-Day (EOD) Backtester

Fetches all ENTER alerts for a given date, simulates the trade
on 5-minute candles from trigger_time to 15:15, and records results.
"""

import logging
from datetime import datetime, time
from sqlalchemy.orm import Session

from database import Alert, BacktestResult, SessionLocal
from upstox_client import get_historical_data, get_ltp

logger = logging.getLogger(__name__)


def parse_trigger_time(time_str: str) -> time | None:
    """
    Parse Chartink's time format: '2:34 pm' or '10:15 am'
    Returns a datetime.time object.
    """
    try:
        time_str = time_str.strip().lower()
        # Try common formats
        for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
            try:
                return datetime.strptime(time_str, fmt).time()
            except ValueError:
                continue
    except Exception:
        pass
    logger.warning(f"Could not parse trigger time: {time_str}")
    return None

def create_pending_trade(alert: Alert, db: Session):
    """Create a PENDING BacktestResult immediately when an ENTER alert fires."""
    existing = db.query(BacktestResult).filter(BacktestResult.alert_id == alert.id).first()
    if existing: return
    
    quantity = int((50000 * 4) / alert.entry_price) if alert.entry_price else 0
    bt_result = BacktestResult(
        alert_id    = alert.id,
        entry_time  = alert.trigger_time,
        entry_price = alert.entry_price,
        outcome     = "PENDING",
        quantity    = quantity,
    )
    db.add(bt_result)
    db.commit()

def track_live_trades():
    """Fetches LTP for all PENDING trades and checks SL/Target."""
    logger.info("Running 1-minute live trade tracker...")
    db = SessionLocal()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        now_time = datetime.now().time()
        
        # Are we at or past square off time?
        force_square_off = now_time >= time(15, 15)
        
        pending_trades = db.query(BacktestResult).join(Alert).filter(
            Alert.trigger_date == today,
            BacktestResult.outcome == "PENDING"
        ).all()
        
        for trade in pending_trades:
            try:
                alert = trade.alert
                ltp = get_ltp(alert.stock)
                if not ltp: continue
                
                outcome = None
                exit_price = None
                
                # Check rules
                if alert.target and ltp >= alert.target:
                    outcome = "PROFIT"
                    exit_price = alert.target
                elif alert.stop_loss and ltp <= alert.stop_loss:
                    outcome = "LOSS"
                    exit_price = alert.stop_loss
                elif force_square_off:
                    outcome = "FLAT" if ltp == alert.entry_price else ("PROFIT" if ltp > alert.entry_price else "LOSS")
                    exit_price = ltp
                    
                if outcome and exit_price is not None:
                    trade.outcome = outcome
                    trade.exit_price = exit_price
                    trade.exit_time = datetime.now().strftime("%I:%M %p")
                    trade.pnl_pct = round((exit_price - alert.entry_price) / alert.entry_price * 100, 2)
                    trade.pnl_amount = round(trade.quantity * (exit_price - alert.entry_price), 2)
                    logger.info(f"Live Track: Locked in {outcome} for {alert.stock} at ₹{exit_price}")
            except Exception as e:
                logger.error(f"Error tracking {trade.alert.stock}: {e}")
                
        db.commit()
    except Exception as e:
        logger.error(f"Live tracker top-level error: {e}")
    finally:
        db.close()

def monitor_wait_pullbacks():
    """Fetches LTP and last closed 5-min candle for WAIT alerts to check for pullbacks."""
    logger.info("Running 1-minute pullback watcher for WAIT stocks...")
    from database import SessionLocal, Alert
    from upstox_client import get_historical_data, get_ltp
    from logic_engine import calculate_vwap, evaluate_wait_upgrade
    from main import _main_event_loop, broadcast_event, _serialize_alert
    import asyncio
    
    db = SessionLocal()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        
        wait_alerts = db.query(Alert).filter(
            Alert.trigger_date == today,
            Alert.verdict == "WAIT"
        ).all()
        
        if not wait_alerts:
            return
            
        for alert in wait_alerts:
            try:
                # 1. Fetch live price
                ltp = get_ltp(alert.stock)
                if not ltp: continue
                
                # 2. Fetch today's 5-minute data
                df = get_historical_data(alert.stock, days=1)
                if df is None or len(df) < 2: continue
                
                # 3. Calculate VWAP
                df["vwap"] = calculate_vwap(df)
                
                # 4. Get last CLOSED candle
                # We drop the very last row assuming it's the currently forming 5-min candle
                last_closed = df.iloc[-2]
                
                # 5. Evaluate upgrade
                result = evaluate_wait_upgrade(last_closed, ltp)
                status = result.get("status")
                
                if status in ("ENTER", "SKIP"):
                    alert.verdict = status
                    alert.verdict_reason = result.get("reason", "")
                    
                    if status == "ENTER":
                        alert.entry_price = result["entry_price"]
                        alert.stop_loss = result["stop_loss"]
                        # 1:2 R:R Target
                        alert.target = round(alert.entry_price + ((alert.entry_price - alert.stop_loss) * 2), 2)
                        
                        logger.info(f"UPGRADED {alert.stock} from WAIT to ENTER at {alert.entry_price}")
                        db.commit() # commit alert changes first
                        create_pending_trade(alert, db)
                    else:
                        logger.info(f"DOWNGRADED {alert.stock} from WAIT to SKIP (Cancelled)")
                        db.commit()

                    # Push SSE event to update UI instantly
                    serialized = _serialize_alert(alert)
                    if _main_event_loop and not _main_event_loop.is_closed():
                        asyncio.run_coroutine_threadsafe(
                            broadcast_event("new_alert", serialized),
                            _main_event_loop
                        )

            except Exception as e:
                logger.error(f"Error checking pullback for {alert.stock}: {e}")
                
    except Exception as e:
        logger.error(f"Pullback watcher top-level error: {e}")
    finally:
        db.close()

def run_backtest(date_str: str | None = None) -> dict:
    """
    Run backtest for all ENTER alerts on a given date.

    Args:
        date_str: 'YYYY-MM-DD'. Defaults to today.

    Returns:
        Summary dict with total, wins, losses, flats, win_rate, net_pnl.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    logger.info(f"Starting backtest for date: {date_str}")
    db: Session = SessionLocal()
    results = {"date": date_str, "processed": 0, "wins": 0, "losses": 0, "flats": 0,
               "win_rate": 0.0, "net_pnl_pct": 0.0, "net_pnl_amount": 0.0, "errors": []}

    try:
        # Fetch all ENTER alerts for this date
        alerts = db.query(Alert).filter(
            Alert.trigger_date == date_str,
            Alert.verdict == "ENTER"
        ).all()

        if not alerts:
            logger.info(f"No ENTER alerts found for {date_str}")
            return results

        for alert in alerts:
            try:
                result = _backtest_single_alert(alert, db)
                if result:
                    results["processed"] += 1
                    if result.outcome == "PROFIT":
                        results["wins"] += 1
                    elif result.outcome == "LOSS":
                        results["losses"] += 1
                    else:
                        results["flats"] += 1
            except Exception as e:
                logger.error(f"Backtest failed for {alert.stock}: {e}")
                results["errors"].append(f"{alert.stock}: {str(e)}")

        db.commit()

        # Summary stats
        total = results["processed"]
        if total > 0:
            results["win_rate"] = round(results["wins"] / total * 100, 1)
            # Fetch all P&L and average them
            bt_results = db.query(BacktestResult).join(Alert).filter(
                Alert.trigger_date == date_str,
                BacktestResult.pnl_pct.isnot(None)
            ).all()
            if bt_results:
                results["net_pnl_pct"] = round(
                    sum(r.pnl_pct for r in bt_results) / len(bt_results), 2
                )
                results["net_pnl_amount"] = round(
                    sum(r.pnl_amount for r in bt_results if r.pnl_amount is not None), 2
                )

    except Exception as e:
        logger.error(f"Backtest run failed: {e}")
        db.rollback()
        results["errors"].append(str(e))
    finally:
        db.close()

    logger.info(f"Backtest complete: {results}")
    return results


def _backtest_single_alert(alert: Alert, db: Session) -> BacktestResult | None:
    """Simulate a single trade and save the result (or update PENDING)."""

    # Check if result already exists and is finalized
    existing = db.query(BacktestResult).filter(
        BacktestResult.alert_id == alert.id
    ).first()
    
    if existing and existing.outcome != "PENDING":
        logger.info(f"Backtest result already finalized for alert {alert.id} ({alert.stock})")
        return existing

    # Parse trigger time
    trigger_time = parse_trigger_time(alert.trigger_time)
    if trigger_time is None:
        logger.warning(f"Skipping {alert.stock} — bad trigger time: {alert.trigger_time}")
        return None

    entry_price = alert.entry_price
    target      = alert.target
    stop_loss   = alert.stop_loss

    if not all([entry_price, target, stop_loss]):
        logger.warning(f"Missing trade params for {alert.stock}")
        return None

    # Fetch 5-min data for the alert date
    try:
        df = get_historical_data(alert.stock, days=1)
    except Exception as e:
        logger.error(f"Could not fetch data for {alert.stock}: {e}")
        return None

    if df.empty:
        return None

    # Filter to candles AFTER trigger_time and up to 15:15
    square_off_time = time(15, 15)
    df["time_only"] = df["timestamp"].dt.time
    post_trigger = df[
        (df["time_only"] >= trigger_time) &
        (df["time_only"] <= square_off_time)
    ].reset_index(drop=True)

    if post_trigger.empty:
        logger.warning(f"No post-trigger candles found for {alert.stock}")
        return None

    # ── Simulate the trade ────────────────────────────────────────────────────
    outcome    = "FLAT"
    exit_price = post_trigger.iloc[-1]["close"]  # Default: square-off at close
    exit_time  = str(post_trigger.iloc[-1]["time_only"])
    entry_time = str(post_trigger.iloc[0]["time_only"])

    for _, candle in post_trigger.iterrows():
        hit_target = candle["high"] >= target
        hit_sl     = candle["low"]  <= stop_loss

        if hit_target and hit_sl:
            # Both in same candle — assume gap-up SL hit first (conservative)
            outcome    = "LOSS"
            exit_price = stop_loss
            exit_time  = str(candle["time_only"])
            break
        elif hit_target:
            outcome    = "PROFIT"
            exit_price = target
            exit_time  = str(candle["time_only"])
            break
        elif hit_sl:
            outcome    = "LOSS"
            exit_price = stop_loss
            exit_time  = str(candle["time_only"])
            break

    pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2)
    quantity = int((50000 * 4) / entry_price)
    pnl_amount = round(quantity * (exit_price - entry_price), 2)

    if existing:
        # Update existing PENDING record
        existing.entry_time = entry_time
        existing.exit_time = exit_time
        existing.exit_price = round(exit_price, 2)
        existing.outcome = outcome
        existing.pnl_pct = pnl_pct
        existing.quantity = quantity
        existing.pnl_amount = pnl_amount
        bt_result = existing
    else:
        bt_result = BacktestResult(
            alert_id    = alert.id,
            entry_time  = entry_time,
            entry_price = entry_price,
            exit_time   = exit_time,
            exit_price  = round(exit_price, 2),
            outcome     = outcome,
            pnl_pct     = pnl_pct,
            quantity    = quantity,
            pnl_amount  = pnl_amount,
        )
        db.add(bt_result)
        
    logger.info(f"  {alert.stock}: {outcome} | P&L: {pnl_pct}%")
    return bt_result

"""
backtest.py — End-of-Day (EOD) Backtester

Fetches all ENTER alerts for a given date, simulates the trade
on 5-minute candles from trigger_time to 15:20, and records results.
"""

import logging
import pytz
from datetime import datetime, time
from sqlalchemy.orm import Session

from database import Alert, BacktestResult, SessionLocal
from upstox_client import get_historical_data, get_ltp, get_ltps

logger = logging.getLogger(__name__)


def parse_trigger_time(time_str: str) -> time | None:
    """
    Parse Chartink's time format: '2:34 pm' or '10:15 am'
    Returns a datetime.time object.
    """
    try:
        time_str = time_str.strip().lower()
        # Try common formats
        for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M:%S", "%H:%M"):
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
    IST = pytz.timezone("Asia/Kolkata")
    db = SessionLocal()
    try:
        now_ist = datetime.now(IST)
        today = now_ist.strftime("%Y-%m-%d")
        now_time = now_ist.time()
        
        # Are we at or past square off time?
        force_square_off = now_time >= time(15, 25)
        
        pending_trades = db.query(BacktestResult).join(Alert).filter(
            Alert.trigger_date == today,
            BacktestResult.outcome == "PENDING"
        ).all()
        
        if not pending_trades:
            return

        pending_stocks = [t.alert.stock for t in pending_trades]
        ltp_map = get_ltps(pending_stocks)
        
        for trade in pending_trades:
            try:
                alert = trade.alert
                ltp = ltp_map.get(alert.stock.upper())
                if not ltp: continue
                
                # Validate SL: SL must be strictly below entry for long positions.
                # If DB has a corrupt SL (SL >= entry), ignore SL check.
                entry = alert.entry_price
                sl = alert.stop_loss
                target = alert.target
                
                sl_valid = sl is not None and sl < entry if entry else False
                
                outcome = None
                exit_price = None
                exit_time = None
                
                # Format current time as "3:25 pm" style
                def _fmt_now():
                    try:
                        return now_ist.strftime("%-I:%M %p").lower()
                    except ValueError:
                        return now_ist.strftime("%I:%M %p").lower().lstrip("0")
                
                # Check rules
                if target and ltp >= target:
                    outcome = "PROFIT"
                    exit_price = target
                    exit_time = _fmt_now()
                elif sl_valid and ltp <= sl:
                    outcome = "LOSS"
                    exit_price = sl
                    exit_time = _fmt_now()
                elif force_square_off:
                    if entry:
                        outcome = "FLAT" if ltp == entry else ("PROFIT" if ltp > entry else "LOSS")
                    else:
                        outcome = "FLAT"
                    exit_price = ltp
                    exit_time = "3:25 pm"  # Force 3:25 PM for auto-square off
                    
                if outcome and exit_price is not None:
                    trade.outcome = outcome
                    trade.exit_price = exit_price
                    trade.exit_time = exit_time
                    if entry:
                        trade.pnl_pct = round((exit_price - entry) / entry * 100, 2)
                        trade.pnl_amount = round(trade.quantity * (exit_price - entry), 2)
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
            
        wait_stocks = [a.stock for a in wait_alerts]
        ltp_map = get_ltps(wait_stocks)
        
        for alert in wait_alerts:
            try:
                # 1. Fetch live price
                ltp = ltp_map.get(alert.stock.upper())
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
    """
    Simulate a single trade and save the result (or update PENDING).

    KEY SIMULATION RULES:
    - The entry is made at the OPEN of the first candle after trigger time.
    - SL / Target are only checked from the SECOND candle onwards (you cannot
      enter and exit in the same 5-min bar).
    - If neither SL nor Target is hit by 15:20, the trade is auto-squared off
      at the CLOSE of the last available candle (which is the 15:20 candle),
      and exit_time is set to "15:25" (Kite auto-square-off).
    - SL must be strictly BELOW entry. If DB has a corrupt SL (SL >= entry),
      the SL check is skipped to avoid recording a phantom loss.
    """

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

    # ── CRITICAL GUARD: SL must be strictly below entry ───────────────────────
    # If Chartink was delayed or a timing edge-case caused SL >= entry in DB,
    # clamp SL to 0.5% below entry rather than skip the trade entirely.
    if stop_loss >= entry_price:
        logger.warning(
            f"Correcting invalid SL for {alert.stock}: SL={stop_loss} >= Entry={entry_price}. "
            f"Clamping to entry * 0.995."
        )
        stop_loss = round(entry_price * 0.995, 2)

    # Fetch 5-min data for the alert date
    try:
        df = get_historical_data(alert.stock, days=1, end_date_str=alert.trigger_date)
    except Exception as e:
        logger.error(f"Could not fetch data for {alert.stock}: {e}")
        return None

    if df.empty:
        return None

    # Filter to candles from trigger_time onwards (including trigger candle as entry candle)
    # Simulation ends at 15:20 — the last candle before Kite auto-squares at 15:25
    square_off_time = time(15, 20)
    df["time_only"] = df["timestamp"].dt.time
    post_trigger = df[
        (df["time_only"] >= trigger_time) &
        (df["time_only"] <= square_off_time)
    ].reset_index(drop=True)

    if post_trigger.empty:
        logger.warning(f"No post-trigger candles found for {alert.stock}")
        return None

    # ── Entry: first candle at or after trigger time ──────────────────────────
    entry_candle = post_trigger.iloc[0]
    entry_time_str = _fmt_time(entry_candle["time_only"])
    
    # If there is no second candle, we have no candle to trade on — use close of entry candle
    # as exit (auto-squared immediately — treat as flat/minimal move)
    if len(post_trigger) < 2:
        exit_price = float(entry_candle["close"])
        exit_time_str = "3:25 pm"
        if exit_price > entry_price:
            outcome = "PROFIT"
        elif exit_price < entry_price:
            outcome = "LOSS"
        else:
            outcome = "FLAT"
        pnl_pct = float(round((exit_price - entry_price) / entry_price * 100, 2))
        quantity = int((50000 * 4) / entry_price)
        pnl_amount = float(round(quantity * (exit_price - entry_price), 2))
        return _save_result(
            alert, existing, db,
            entry_time_str, entry_price, exit_time_str, exit_price,
            outcome, pnl_pct, quantity, pnl_amount
        )

    # ── Simulation: check candles AFTER the entry candle ─────────────────────
    trade_candles = post_trigger.iloc[1:]  # skip first (entry) candle

    outcome    = None
    exit_price = None
    exit_time_str = None

    for _, candle in trade_candles.iterrows():
        hit_target = candle["high"] >= target
        hit_sl     = candle["low"]  <= stop_loss

        if hit_target and hit_sl:
            # Both in same candle — assume SL hit first (conservative for a long trade)
            outcome       = "LOSS"
            exit_price    = stop_loss
            exit_time_str = _fmt_time(candle["time_only"])
            break
        elif hit_target:
            outcome       = "PROFIT"
            exit_price    = target
            exit_time_str = _fmt_time(candle["time_only"])
            break
        elif hit_sl:
            outcome       = "LOSS"
            exit_price    = stop_loss
            exit_time_str = _fmt_time(candle["time_only"])
            break

    if outcome is None:
        # Neither target nor SL was hit → auto-squared off at 3:25 PM.
        # Exit price = close of last available candle (15:20 bar).
        exit_price    = float(post_trigger.iloc[-1]["close"])
        exit_time_str = "3:25 pm"
        if exit_price > entry_price:
            outcome = "PROFIT"
        elif exit_price < entry_price:
            outcome = "LOSS"
        else:
            outcome = "FLAT"

    exit_price = float(exit_price)
    pnl_pct    = float(round((exit_price - entry_price) / entry_price * 100, 2))
    quantity   = int((50000 * 4) / entry_price)
    pnl_amount = float(round(quantity * (exit_price - entry_price), 2))

    return _save_result(
        alert, existing, db,
        entry_time_str, entry_price, exit_time_str, exit_price,
        outcome, pnl_pct, quantity, pnl_amount
    )


def _fmt_time(t: time) -> str:
    """Convert a datetime.time to human-readable '9:15 am' style string."""
    try:
        # Use a datetime for strftime convenience
        dt = datetime(2000, 1, 1, t.hour, t.minute, t.second)
        try:
            return dt.strftime("%-I:%M %p").lower()
        except ValueError:
            return dt.strftime("%I:%M %p").lower().lstrip("0")
    except Exception:
        return str(t)


def _save_result(
    alert: Alert, existing: BacktestResult | None, db: Session,
    entry_time: str, entry_price: float, exit_time: str, exit_price: float,
    outcome: str, pnl_pct: float, quantity: int, pnl_amount: float
) -> BacktestResult:
    """Upsert a BacktestResult record."""
    if existing:
        existing.entry_time  = entry_time
        existing.exit_time   = exit_time
        existing.exit_price  = round(exit_price, 2)
        existing.outcome     = outcome
        existing.pnl_pct     = pnl_pct
        existing.quantity    = quantity
        existing.pnl_amount  = pnl_amount
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
        
    logger.info(f"  {alert.stock}: {outcome} | P&L: {pnl_pct}% (₹{pnl_amount})")
    return bt_result

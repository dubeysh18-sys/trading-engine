"""
main.py — FastAPI application entry point

Routes:
  POST /api/webhook/chartink     — Receive Chartink alerts
  GET  /api/alerts               — Today's alerts + verdicts
  GET  /api/nifty-status         — NIFTY LTP vs VWAP live check
  POST /api/run-backtest         — Manual backtest trigger
  GET  /api/backtest-results     — Today's backtest results + summary
  GET  /health                   — Keep-alive ping (for UptimeRobot)
"""

import logging
import os
import pytz
import smtplib
from datetime import datetime
from contextlib import asynccontextmanager
from collections import deque
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import json
import asyncio
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

from database import init_db, get_db, Alert, BacktestResult
from upstox_client import get_historical_data, get_ltp, resolve_instrument_key
from logic_engine import evaluate_rules, get_indicator_snapshot
from backtest import run_backtest, track_live_trades

# In-memory debug log ring buffer (last 200 entries)
_debug_log: deque = deque(maxlen=200)

class _DebugHandler(logging.Handler):
    """Captures log records into the in-memory ring buffer for /api/debug-log."""
    def emit(self, record: logging.LogRecord):
        _debug_log.append({
            "time": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "msg": self.format(record),
        })

# SSE Clients
clients = []

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Attach debug handler to root logger so all modules are captured
_dh = _DebugHandler()
_dh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_dh)

IST = pytz.timezone("Asia/Kolkata")

# ── Scheduler ─────────────────────────────────────────────────────────────────
scheduler = BackgroundScheduler(timezone=IST)


def _scheduled_backtest():
    """Triggered at 15:30 IST on market days to clean up any remaining trades."""
    logger.info("Scheduled backtest (EOD square-off) starting at 15:30 IST...")
    try:
        result = run_backtest()
        logger.info(f"Scheduled backtest done: {result}")
    except Exception as e:
        logger.error(f"Scheduled backtest failed: {e}")

def _live_tracker():
    """Triggered every 1 minute during market hours."""
    try:
        track_live_trades()
    except Exception as e:
        logger.error(f"Live tracker failed: {e}")


def _pullback_watcher():
    """Triggered every 1 minute to check WAIT stocks for pullbacks."""
    try:
        from backtest import monitor_wait_pullbacks
        monitor_wait_pullbacks()
    except Exception as e:
        logger.error(f"Pullback watcher failed: {e}")


# ── App Lifecycle ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_event_loop
    logger.info("Starting Trading Rule Engine...")
    _main_event_loop = asyncio.get_running_loop()  # capture loop for background threads
    init_db()
    # Schedule EOD backtest at 15:30 IST, Mon-Fri
    scheduler.add_job(
        _scheduled_backtest,
        trigger="cron",
        hour=15, minute=30,
        day_of_week="mon-fri",
        id="eod_backtest"
    )
    # Schedule Live Tracker every minute between 9:15 and 15:30
    scheduler.add_job(
        _live_tracker,
        trigger="cron",
        hour="9-15", minute="*",
        day_of_week="mon-fri",
        id="live_tracker"
    )
    # Schedule Pullback Watcher every minute between 9:15 and 15:30
    scheduler.add_job(
        _pullback_watcher,
        trigger="cron",
        hour="9-15", minute="*",
        day_of_week="mon-fri",
        id="pullback_watcher"
    )
    scheduler.start()
    logger.info("APScheduler started — Live tracking & Pullback Watcher active. EOD backtest at 15:30.")

    yield
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped.")


# ── FastAPI App ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Trading Rule Engine",
    description="Chartink → Upstox → 4 Golden Rules → Dashboard",
    version="1.0.0",
    lifespan=lifespan
)

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tightened in production via env
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request Models ─────────────────────────────────────────────────────────────
class ChartinkWebhook(BaseModel):
    stocks:          str
    trigger_prices:  str | None = None
    triggered_at:    str | None = None
    scan_name:       str | None = None
    scan_url:        str | None = None
    alert_name:      str | None = None
    webhook_url:     str | None = None


# ── Email Notifications ───────────────────────────────────────────────────────
def send_enter_email(stock: str, entry: float, target: float, stop_loss: float,
                     scan_name: str, trigger_time: str):
    """
    Sends an HTML email for every ENTER verdict.
    Requires SMTP_USER and SMTP_PASSWORD in .env (Gmail App Password).
    Fails silently so it never breaks the main alert flow.
    """
    smtp_user  = os.getenv("SMTP_USER", "")
    smtp_pass  = os.getenv("SMTP_PASSWORD", "")
    to_email   = os.getenv("NOTIFICATION_EMAIL", "dubeysh18@gmail.com")

    if not smtp_user or not smtp_pass:
        logger.warning("Email: SMTP_USER / SMTP_PASSWORD not set — skipping email")
        return

    try:
        sl_pct     = round((entry - stop_loss) / entry * 100, 2)
        target_pct = round((target - entry) / entry * 100, 2)
        rr         = round(target_pct / sl_pct, 1) if sl_pct else "N/A"

        html = f"""
<html><body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,sans-serif">
<div style="max-width:560px;margin:24px auto;background:#0f1117;border-radius:14px;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,.4)">
  <div style="background:linear-gradient(135deg,#16a34a,#22c55e);padding:24px 28px;text-align:center">
    <div style="font-size:36px;margin-bottom:6px">🟢</div>
    <h1 style="margin:0;font-size:22px;color:#fff;letter-spacing:.5px">ENTER Signal Detected</h1>
    <p style="margin:6px 0 0;color:rgba(255,255,255,.75);font-size:13px">{scan_name} &bull; {trigger_time}</p>
  </div>
  <div style="padding:24px 28px">
    <div style="font-size:36px;font-weight:700;color:#22c55e;font-family:monospace;letter-spacing:1px">{stock}</div>
    <div style="display:flex;gap:12px;margin:20px 0">
      <div style="flex:1;background:#1e2d45;border-radius:10px;padding:16px;text-align:center">
        <div style="font-size:10px;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.08em">Entry</div>
        <div style="font-size:22px;font-weight:700;color:#22c55e;font-family:monospace">&zwj;&#8377;{entry:.2f}</div>
      </div>
      <div style="flex:1;background:#1e2d45;border-radius:10px;padding:16px;text-align:center">
        <div style="font-size:10px;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.08em">Target</div>
        <div style="font-size:22px;font-weight:700;color:#60a5fa;font-family:monospace">&zwj;&#8377;{target:.2f}</div>
        <div style="font-size:11px;color:#4b5563;margin-top:2px">+{target_pct}%</div>
      </div>
      <div style="flex:1;background:#1e2d45;border-radius:10px;padding:16px;text-align:center">
        <div style="font-size:10px;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.08em">Stop Loss</div>
        <div style="font-size:22px;font-weight:700;color:#f87171;font-family:monospace">&zwj;&#8377;{stop_loss:.2f}</div>
        <div style="font-size:11px;color:#4b5563;margin-top:2px">-{sl_pct}%</div>
      </div>
    </div>
    <div style="background:#1e2d45;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:#94a3b8">
      Risk/Reward: <strong style="color:#e2e8f0">{rr}:1</strong>
    </div>
    <a href="https://kite.zerodha.com/chart/web/ciq/NSE/{stock}/EQ"
       style="display:block;background:#3b82f6;color:#fff;text-align:center;padding:13px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px"
    >View Chart on Kite &rarr;</a>
  </div>
  <div style="padding:12px 28px;background:#0a0e17;text-align:center;font-size:10px;color:#4b5563">
    Automated alert from Trading Rule Engine &bull; Not financial advice
  </div>
</div>
</body></html>
"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"\U0001f7e2 ENTER: {stock} @ \u20b9{entry:.0f} | T:{target:.0f} SL:{stop_loss:.0f}"
        msg["From"]    = smtp_user
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())

        logger.info(f"Email sent for {stock} ENTER to {to_email}")
    except Exception as e:
        logger.error(f"Email send failed for {stock}: {e}")


# ── Keep-alive ─────────────────────────────────────────────────────────────────
@app.get("/health")
def health_check():
    """UptimeRobot pings this every 14 min to keep Render awake."""
    return {"status": "ok", "time": datetime.now(IST).isoformat()}


# ── Debug Log ──────────────────────────────────────────────────────────────────
@app.get("/api/debug-log")
def get_debug_log(level: str = Query(None)):
    """
    Returns the last 200 in-memory log entries for live debugging.
    Filter by level: ?level=ERROR or ?level=WARNING
    """
    logs = list(_debug_log)
    if level:
        logs = [l for l in logs if l["level"] == level.upper()]
    return {"count": len(logs), "logs": logs[-100:]}


# ── Webhook Receiver ───────────────────────────────────────────────────────────
@app.post("/api/webhook/chartink")
async def receive_chartink_alert(
    payload: ChartinkWebhook,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Receive a Chartink webhook, parse the stock list, and kick off
    rule evaluation in the background for each stock.
    """
    stocks = [s.strip().upper() for s in payload.stocks.split(",") if s.strip()]
    prices_raw = payload.trigger_prices or ""
    prices_list = [p.strip() for p in prices_raw.split(",")]

    # Build a dict: symbol → trigger_price (or None)
    stock_price_map = {}
    for i, stock in enumerate(stocks):
        try:
            price = float(prices_list[i]) if i < len(prices_list) else None
        except (ValueError, IndexError):
            price = None
        stock_price_map[stock] = price

    # Use %-I on Linux (no zero-pad), fall back to %I for Windows/other platforms
    try:
        _time_fmt = datetime.now(IST).strftime("%-I:%M %p")
    except ValueError:
        _time_fmt = datetime.now(IST).strftime("%I:%M %p").lstrip("0")
    trigger_time  = payload.triggered_at or _time_fmt
    trigger_date  = datetime.now(IST).strftime("%Y-%m-%d")
    scan_name     = payload.scan_name or "Unknown Scan"
    alert_name    = payload.alert_name or ""

    logger.info(f"Webhook: {scan_name} | {len(stocks)} stocks | {trigger_time}")

    # Queue processing in background so webhook returns fast
    background_tasks.add_task(
        _process_alerts,
        stock_price_map, trigger_time, trigger_date, scan_name, alert_name
    )

    return {
        "status": "accepted",
        "stocks_queued": stocks,
        "trigger_time": trigger_time,
        "message": f"Processing {len(stocks)} stock(s) in background"
    }


# Global reference to the main event loop, set on startup
_main_event_loop: asyncio.AbstractEventLoop | None = None


def _process_alerts(
    stock_price_map: dict,
    trigger_time: str,
    trigger_date: str,
    scan_name: str,
    alert_name: str
):
    """Background task: fetch data, evaluate rules, save to DB."""
    from database import SessionLocal
    db = SessionLocal()

    try:
        # Pre-fetch NIFTY data once for all stocks in this batch
        logger.info("Fetching NIFTY 5-min data...")
        nifty_df = None
        nifty_fetch_error = None
        try:
            nifty_df = get_historical_data("NIFTY50", days=3)
        except Exception as e:
            nifty_fetch_error = str(e)
            logger.error(f"NIFTY data fetch failed: {e}")

        for stock, trigger_price in stock_price_map.items():
            alert = None
            try:
                logger.info(f"Processing {stock} @ ₹{trigger_price}...")

                try:
                    stock_df = get_historical_data(stock, days=3)
                    result   = evaluate_rules(
                        stock_df=stock_df,
                        nifty_df=nifty_df if nifty_df is not None else stock_df,
                        trigger_price=trigger_price
                    )
                except Exception as fetch_err:
                    # ── Fault-tolerant fallback ──────────────────────────────
                    # Save the alert as ERROR so the dashboard always shows it
                    logger.error(f"Data fetch/rule eval failed for {stock}: {fetch_err}")
                    result = {
                        "verdict": "ERROR",
                        "verdict_reason": f"Data unavailable: {fetch_err}",
                        "entry": None, "target": None, "stop_loss": None,
                        "vwap": None, "ema9": None,
                        "pivot_r1": None, "pivot_r2": None, "pivot_s1": None,
                        "upper_wick": None, "solid_body": None,
                        "nifty_ltp": None, "nifty_vwap": None,
                    }

                # Helper: convert numpy scalars → plain Python float so
                # psycopg2 doesn't misinterpret np.float64 as a schema name
                def _f(v):
                    if v is None:
                        return None
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return None

                alert = Alert(
                    scan_name     = scan_name,
                    alert_name    = alert_name,
                    stock         = stock,
                    trigger_time  = trigger_time,
                    trigger_date  = trigger_date,
                    trigger_price = _f(trigger_price),
                    entry_price   = _f(result["entry"]),
                    target        = _f(result["target"]),
                    stop_loss     = _f(result["stop_loss"]),
                    vwap          = _f(result["vwap"]),
                    ema9          = _f(result["ema9"]),
                    pivot_r1      = _f(result["pivot_r1"]),
                    pivot_r2      = _f(result["pivot_r2"]),
                    pivot_s1      = _f(result["pivot_s1"]),
                    upper_wick    = _f(result["upper_wick"]),
                    solid_body    = _f(result["solid_body"]),
                    nifty_ltp     = _f(result["nifty_ltp"]),
                    nifty_vwap    = _f(result["nifty_vwap"]),
                    verdict       = result["verdict"],
                    verdict_reason= result["verdict_reason"],
                )
                db.add(alert)

                # If ENTER, immediately create a PENDING backtest result for live tracking
                if alert.verdict == "ENTER":
                    db.flush()  # get alert id
                    from backtest import create_pending_trade
                    create_pending_trade(alert, db)

                db.commit()
                logger.info(f"  ✓ Saved {stock}: {result['verdict']} | {result['verdict_reason']}")

                # Send email notification for ENTER signals (non-blocking)
                if alert.verdict == "ENTER" and alert.entry_price and alert.target and alert.stop_loss:
                    import threading
                    threading.Thread(
                        target=send_enter_email,
                        args=(stock, alert.entry_price, alert.target, alert.stop_loss, scan_name, trigger_time),
                        daemon=True
                    ).start()

                # ── Push to SSE clients (thread-safe) ──────────────────────
                serialized = _serialize_alert(alert)
                if _main_event_loop and not _main_event_loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        broadcast_event("new_alert", serialized),
                        _main_event_loop
                    )

            except Exception as e:
                logger.error(f"Error saving alert for {stock}: {e}", exc_info=True)
                db.rollback()

    finally:
        db.close()

async def broadcast_event(event_type: str, data: dict):
    message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    for q in clients:
        await q.put(message)


# ── Alert Feed ─────────────────────────────────────────────────────────────────
@app.get("/api/alerts")
def get_alerts(date: str = Query(None), db: Session = Depends(get_db)):
    """Return alerts for a specific date, newest first."""
    query_date = date or datetime.now(IST).strftime("%Y-%m-%d")
    alerts = (
        db.query(Alert)
        .filter(Alert.trigger_date == query_date)
        .order_by(Alert.created_at.desc())
        .all()
    )
    return [_serialize_alert(a) for a in alerts]


def _serialize_alert(a: Alert) -> dict:
    return {
        "id":            a.id,
        "stock":         a.stock,
        "scan_name":     a.scan_name,
        "trigger_time":  a.trigger_time,
        "trigger_date":  a.trigger_date,
        "trigger_price": a.trigger_price,
        "entry":         a.entry_price,
        "target":        a.target,
        "stop_loss":     a.stop_loss,
        "vwap":          a.vwap,
        "ema9":          a.ema9,
        "pivot_r1":      a.pivot_r1,
        "verdict":       a.verdict,
        "verdict_reason":a.verdict_reason,
        "created_at":    a.created_at.isoformat() if a.created_at else None,
    }

@app.get("/api/alerts/stream")
async def alerts_stream():
    """Server-Sent Events for instant UI updates."""
    q = asyncio.Queue()
    clients.append(q)
    
    async def event_generator():
        try:
            while True:
                message = await q.get()
                yield message
        except asyncio.CancelledError:
            pass
        finally:
            clients.remove(q)
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Live Price Tracker ──────────────────────────────────────────────────────────
@app.get("/api/alerts/live-prices")
def get_live_prices(date: str = Query(None), db: Session = Depends(get_db)):
    """
    For all ENTER alerts on a date, return the current LTP + P&L status.
    Used by the frontend to show live trade progress bars and P&L.
    """
    query_date = date or datetime.now(IST).strftime("%Y-%m-%d")
    enter_alerts = (
        db.query(Alert)
        .filter(Alert.trigger_date == query_date, Alert.verdict == "ENTER")
        .all()
    )

    result = []
    for alert in enter_alerts:
        ltp = None
        pnl_pct = None
        status = "PENDING"

        # Check if a finalized backtest result exists
        bt = alert.backtest_result
        if bt and bt.outcome not in ("PENDING", None):
            status = bt.outcome  # PROFIT | LOSS | FLAT
            ltp = bt.exit_price
            pnl_pct = bt.pnl_pct
        else:
            # Still active — fetch live LTP
            try:
                ltp = get_ltp(alert.stock)
                if ltp and alert.entry_price:
                    pnl_pct = round((ltp - alert.entry_price) / alert.entry_price * 100, 2)
                    if alert.target and ltp >= alert.target:
                        status = "PROFIT"
                    elif alert.stop_loss and ltp <= alert.stop_loss:
                        status = "LOSS"
                    else:
                        status = "ACTIVE"
            except Exception as e:
                logger.warning(f"live-prices: could not fetch LTP for {alert.stock}: {e}")

        result.append({
            "stock":       alert.stock,
            "alert_id":    alert.id,
            "entry_price": alert.entry_price,
            "target":      alert.target,
            "stop_loss":   alert.stop_loss,
            "ltp":         ltp,
            "pnl_pct":     pnl_pct,
            "status":      status,
        })

    return result


# ── NIFTY Status ───────────────────────────────────────────────────────────────
@app.get("/api/nifty-status")
def nifty_status(db: Session = Depends(get_db)):
    """
    Return NIFTY LTP and VWAP for the Market Wind indicator.
    VWAP is calculated from today's stored alerts or live fetch.
    """
    try:
        ltp = get_ltp("NIFTY50")
        # Get NIFTY VWAP from latest alert that has it stored
        today = datetime.now(IST).strftime("%Y-%m-%d")
        latest = (
            db.query(Alert)
            .filter(Alert.trigger_date == today, Alert.nifty_vwap.isnot(None))
            .order_by(Alert.created_at.desc())
            .first()
        )
        nifty_vwap = latest.nifty_vwap if latest else None

        # If no stored VWAP, calculate from live data
        if nifty_vwap is None:
            try:
                nifty_df = get_historical_data("NIFTY50", days=1)
                from logic_engine import get_indicator_snapshot
                snap = get_indicator_snapshot(nifty_df)
                nifty_vwap = snap.get("vwap")
            except Exception:
                pass

        is_bullish = (ltp >= nifty_vwap) if (ltp and nifty_vwap) else None
        return {
            "ltp":        ltp,
            "vwap":       nifty_vwap,
            "is_bullish": is_bullish,
            "error":      None
        }
    except Exception as e:
        logger.error(f"NIFTY status error: {e}")
        return {"ltp": None, "vwap": None, "is_bullish": None, "error": str(e)}


# ── Backtest ───────────────────────────────────────────────────────────────────
@app.post("/api/run-backtest")
async def trigger_backtest(background_tasks: BackgroundTasks):
    """Manual trigger for EOD backtest. Runs in background."""
    today = datetime.now(IST).strftime("%Y-%m-%d")
    background_tasks.add_task(run_backtest, today)
    return {"status": "started", "date": today, "message": "Backtest running in background. Refresh results in ~30 seconds."}


@app.get("/api/backtest-results")
def get_backtest_results(date: str = Query(None), db: Session = Depends(get_db)):
    """Return backtest results with summary stats."""
    query_date = date or datetime.now(IST).strftime("%Y-%m-%d")

    results = (
        db.query(BacktestResult, Alert)
        .join(Alert, BacktestResult.alert_id == Alert.id)
        .filter(Alert.trigger_date == query_date)
        .order_by(Alert.created_at.desc())
        .all()
    )

    trades = []
    for bt, alert in results:
        trades.append({
            "stock":        alert.stock,
            "entry_time":   bt.entry_time,
            "entry_price":  bt.entry_price,
            "exit_time":    bt.exit_time,
            "exit_price":   bt.exit_price,
            "outcome":      bt.outcome,
            "pnl_pct":      bt.pnl_pct,
            "quantity":     bt.quantity,
            "pnl_amount":   bt.pnl_amount,
            "target":       alert.target,
            "stop_loss":    alert.stop_loss,
        })

    # Summary
    total   = len(trades)
    wins    = sum(1 for t in trades if t["outcome"] == "PROFIT")
    losses  = sum(1 for t in trades if t["outcome"] == "LOSS")
    flats   = sum(1 for t in trades if t["outcome"] == "FLAT")
    win_rate = round(wins / total * 100, 1) if total > 0 else 0

    # Total ENTER alerts today
    enter_count = db.query(Alert).filter(
        Alert.trigger_date == query_date,
        Alert.verdict == "ENTER"
    ).count()

    pnls = [t["pnl_pct"] for t in trades if t["pnl_pct"] is not None]
    net_pnl = round(sum(pnls) / len(pnls), 2) if pnls else 0
    pnl_amounts = [t["pnl_amount"] for t in trades if t["pnl_amount"] is not None]
    net_pnl_amount = round(sum(pnl_amounts), 2) if pnl_amounts else 0

    return {
        "summary": {
            "total_enter_alerts": enter_count,
            "backtested":         total,
            "wins":               wins,
            "losses":             losses,
            "flats":              flats,
            "win_rate_pct":       win_rate,
            "net_pnl_pct":        net_pnl,
            "net_pnl_amount":     net_pnl_amount,
        },
        "trades": trades
    }


# ── Admin / Recovery ───────────────────────────────────────────────────────────

@app.post("/api/admin/reprocess-errors")
async def reprocess_error_alerts(
    background_tasks: BackgroundTasks,
    date: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    Re-run rule evaluation for all ERROR alerts on a given date.
    Use this after fixing UPSTOX_ACCESS_TOKEN to recover missed verdicts.
    """
    query_date = date or datetime.now(IST).strftime("%Y-%m-%d")
    error_alerts = (
        db.query(Alert)
        .filter(Alert.trigger_date == query_date, Alert.verdict == "ERROR")
        .all()
    )

    if not error_alerts:
        return {"status": "nothing_to_reprocess", "date": query_date, "count": 0}

    # Rebuild the stock_price_map grouped by scan+time
    stock_price_map = {a.stock: a.trigger_price for a in error_alerts}
    scan_name   = error_alerts[0].scan_name
    alert_name  = error_alerts[0].alert_name
    trigger_time = error_alerts[0].trigger_time

    # Delete ERROR records so _process_alerts can insert fresh ones
    for a in error_alerts:
        db.delete(a)
    db.commit()

    background_tasks.add_task(
        _process_alerts,
        stock_price_map, trigger_time, query_date, scan_name, alert_name
    )

    logger.info(f"Reprocessing {len(error_alerts)} ERROR alerts for {query_date}")
    return {
        "status": "reprocessing",
        "date": query_date,
        "count": len(error_alerts),
        "stocks": list(stock_price_map.keys()),
        "message": "Check /api/alerts in ~30 seconds for updated verdicts"
    }


@app.delete("/api/admin/clear-date")
def clear_alerts_for_date(
    date: str = Query(..., description="Date to clear, e.g. 2026-05-22"),
    verdict: str = Query(None, description="Only delete alerts with this verdict, e.g. ERROR"),
    db: Session = Depends(get_db)
):
    """
    Delete all alerts (and backtest results) for a given date.
    Optionally filter by verdict=ERROR to only remove garbage data.
    """
    query = db.query(Alert).filter(Alert.trigger_date == date)
    if verdict:
        query = query.filter(Alert.verdict == verdict.upper())

    alerts = query.all()
    count = len(alerts)

    for alert in alerts:
        if alert.backtest_result:
            db.delete(alert.backtest_result)
        db.delete(alert)
    db.commit()

    logger.info(f"Admin cleared {count} alerts for {date} (verdict filter: {verdict})")
    return {"status": "cleared", "date": date, "deleted_count": count, "verdict_filter": verdict}


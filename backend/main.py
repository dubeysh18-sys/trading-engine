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
import threading
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
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import init_db, get_db, Alert, BacktestResult, engine
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
scheduler = AsyncIOScheduler(timezone=IST)


def _scheduled_backtest():
    """Triggered at 15:30 IST on market days to clean up any remaining trades."""
    logger.info("Scheduled backtest (EOD square-off) starting at 15:30 IST...")
    from database import SessionLocal
    db = SessionLocal()
    try:
        result = run_backtest()
        logger.info(f"Scheduled backtest done: {result}")
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Scheduled backtest failed: {e}")
    finally:
        db.close()

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


def _self_keepalive():
    """Pings our own health endpoint to prevent Render free tier from spinning down."""
    import requests as _req
    try:
        base_url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:10000")
        _req.get(f"{base_url}/health", timeout=10)
        logger.debug("Self-keepalive ping sent.")
    except Exception as e:
        logger.debug(f"Self-keepalive ping failed (non-critical): {e}")


from sqlalchemy import text, inspect as sa_inspect

def run_startup_migrations(engine):
    """Safely adds any missing columns without breaking existing data."""
    inspector = sa_inspect(engine)
    try:
        existing_columns = [col["name"] for col in inspector.get_columns("alerts")]
        
        with engine.connect() as conn:
            if "pivot_r3" not in existing_columns:
                conn.execute(text("ALTER TABLE alerts ADD COLUMN pivot_r3 REAL"))
                conn.commit()
                logger.info("[Migration] Added pivot_r3 column to alerts table.")
            else:
                logger.info("[Migration] pivot_r3 already exists. Skipping.")
    except Exception as e:
        logger.error(f"[Migration] Startup migration check failed: {e}")


# ── App Lifecycle ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_event_loop
    logger.info("Starting Trading Rule Engine...")
    run_startup_migrations(engine)        # ← runs migration on every boot
    _main_event_loop = asyncio.get_event_loop()  # capture loop for background threads
    init_db()
    # Schedule EOD backtest at 15:30 IST, Mon-Fri
    scheduler.add_job(
        _scheduled_backtest,
        trigger="cron",
        hour=15, minute=30,
        day_of_week="mon-fri",
        id="eod_backtest"
    )
    # Schedule Live Tracker every minute between 9:00 and 16:00 IST
    # Wide window catches 9:15 AM open, 3:25 PM square-off, and EOD candle data
    scheduler.add_job(
        _live_tracker,
        trigger="cron",
        hour="9-16", minute="*",
        day_of_week="mon-fri",
        id="live_tracker"
    )
    # Schedule Pullback Watcher every minute between 9:00 and 16:00 IST
    scheduler.add_job(
        _pullback_watcher,
        trigger="cron",
        hour="9-16", minute="*",
        day_of_week="mon-fri",
        id="pullback_watcher"
    )
    # Self-keepalive: ping our own health every 5 minutes to prevent Render from sleeping
    # This is the critical job — Render free tier spins down after 15 min of inactivity
    # Without this, the live tracker and EOD backtest will never fire during market hours
    scheduler.add_job(
        _self_keepalive,
        trigger="cron",
        minute="*/5",
        id="self_keepalive"
    )
    scheduler.start()
    logger.info("APScheduler started — Live tracking & Pullback Watcher active. EOD backtest at 15:30. Self-keepalive every 5 min.")

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
email_lock = threading.Lock()

def send_enter_email(stock: str, entry: float, target: float, stop_loss: float,
                      scan_name: str, trigger_time: str):
    """
    Sends an HTML email for every ENTER verdict.
    Requires SMTP_USER and SMTP_PASSWORD in .env (Gmail App Password).
    Fails silently so it never breaks the main alert flow.
    """
    import time
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
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,sans-serif">
<div style="max-width:560px;margin:24px auto;background:#0f1117;border-radius:14px;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,.4)">
  <div style="background:linear-gradient(135deg,#16a34a,#22c55e);padding:24px 28px;text-align:center">
    <div style="font-size:36px;margin-bottom:6px">🟢</div>
    <h1 style="margin:0;font-size:22px;color:#fff;letter-spacing:.5px">ENTER Signal Detected</h1>
    <p style="margin:6px 0 0;color:rgba(255,255,255,.75);font-size:13px">{scan_name} &bull; {trigger_time}</p>
  </div>
  <div style="padding:24px 28px">
    <div style="font-size:32px;font-weight:700;color:#ffffff;font-family:monospace;letter-spacing:1px;text-align:center;margin-bottom:12px">{stock}</div>
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0">
      <tr>
        <td width="32%" style="background:#1e2d45;border-radius:10px;padding:16px 8px;text-align:center">
          <div style="font-size:10px;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.08em">Entry</div>
          <div style="font-size:18px;font-weight:700;color:#22c55e;font-family:monospace">&zwj;&#8377;{entry:.2f}</div>
        </td>
        <td width="2%"></td>
        <td width="32%" style="background:#1e2d45;border-radius:10px;padding:16px 8px;text-align:center">
          <div style="font-size:10px;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.08em">Target</div>
          <div style="font-size:18px;font-weight:700;color:#60a5fa;font-family:monospace">&zwj;&#8377;{target:.2f}</div>
          <div style="font-size:11px;color:#4b5563;margin-top:2px">+{target_pct}%</div>
        </td>
        <td width="2%"></td>
        <td width="32%" style="background:#1e2d45;border-radius:10px;padding:16px 8px;text-align:center">
          <div style="font-size:10px;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.08em">Stop Loss</div>
          <div style="font-size:18px;font-weight:700;color:#f87171;font-family:monospace">&zwj;&#8377;{stop_loss:.2f}</div>
          <div style="font-size:11px;color:#4b5563;margin-top:2px">-{sl_pct}%</div>
        </td>
      </tr>
    </table>
    <div style="background:#1e2d45;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:#94a3b8;text-align:center">
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

        with email_lock:
            for attempt in range(3):
                try:
                    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
                        server.login(smtp_user, smtp_pass)
                        server.sendmail(smtp_user, to_email, msg.as_string())
                    logger.info(f"Email sent for {stock} ENTER to {to_email} (attempt {attempt+1})")
                    return
                except Exception as smtp_err:
                    logger.warning(f"Email send attempt {attempt+1} failed for {stock}: {smtp_err}")
                    if attempt < 2:
                        time.sleep(2)
            logger.error(f"Email send completely failed for {stock} after 3 attempts")
    except Exception as e:
        logger.error(f"Email template/setup failed for {stock}: {e}")


# ── Keep-alive ─────────────────────────────────────────────────────────────────
@app.get("/health")
def health_check():
    """UptimeRobot pings this every 14 min to keep Render awake."""
    return {"status": "ok", "time": datetime.now(IST).isoformat()}

@app.get("/api/test-email")
def test_email():
    """Directly tests the SMTP configuration and returns exact errors."""
    smtp_user  = os.getenv("SMTP_USER", "")
    smtp_pass  = os.getenv("SMTP_PASSWORD", "")
    to_email   = os.getenv("NOTIFICATION_EMAIL", "dubeysh18@gmail.com")
    
    if not smtp_user or not smtp_pass:
        return {"success": False, "error": "SMTP_USER or SMTP_PASSWORD is not set in environment."}
        
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Test Email from Trading Engine"
        msg["From"]    = smtp_user
        msg["To"]      = to_email
        msg.attach(MIMEText("If you are reading this, your email configuration works!", "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
            
        return {"success": True, "message": f"Test email sent successfully to {to_email} via {smtp_user}"}
    except Exception as e:
        return {"success": False, "error": str(e), "type": type(e).__name__}


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


@app.get("/api/temp-query")
def temp_query(db: Session = Depends(get_db)):
    try:
        from sqlalchemy import text
        res = db.execute(text("SELECT stock, trigger_time, trigger_date, vwap, stop_loss, entry_price FROM alerts WHERE trigger_date = '2026-05-26' ORDER BY trigger_time;"))
        out = []
        for r in res:
            out.append({
                "stock": r[0],
                "trigger_time": r[1],
                "trigger_date": r[2],
                "vwap": r[3],
                "stop_loss": r[4],
                "entry_price": r[5]
            })
        return out
    except Exception as e:
        return {"error": str(e)}


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
                    pivot_r3      = _f(result["pivot_r3"]),
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
        "pivot_r2":      a.pivot_r2,
        "pivot_r3":      a.pivot_r3,
        "pivot_s1":      a.pivot_s1,
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
async def trigger_backtest(
    background_tasks: BackgroundTasks,
    target_date: str = Query(None)
):
    """Manual trigger for EOD backtest. Runs in background."""
    date_str = target_date or datetime.now(IST).strftime("%Y-%m-%d")
    background_tasks.add_task(run_backtest, date_str)
    return {"status": "started", "date": date_str, "message": f"Backtest running in background for {date_str}. Refresh results in ~30 seconds."}


def format_time_str(time_str: str | None) -> str:
    """Normalize any time string to '9:15 am' / '3:25 pm' style (no leading zero)."""
    if not time_str:
        return "—"
    time_str = time_str.strip().lower()
    # Try all known formats, including already-formatted ones
    for fmt_pat in ("%I:%M %p", "%I:%M%p", "%H:%M:%S", "%H:%M"):
        try:
            dt = datetime.strptime(time_str, fmt_pat)
            formatted = dt.strftime("%I:%M %p").lower()
            # Remove leading zero from hour (e.g. '09:15 am' → '9:15 am')
            if formatted.startswith("0"):
                formatted = formatted[1:]
            return formatted
        except ValueError:
            continue
    # Return as-is if no format matched (e.g. already clean '3:25 pm')
    return time_str

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
            "entry_time":   format_time_str(bt.entry_time),
            "entry_price":  bt.entry_price,
            "exit_time":    format_time_str(bt.exit_time) if bt.outcome != "PENDING" else "—",
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

@app.post("/api/admin/run-recovery")
def run_production_recovery(db: Session = Depends(get_db)):
    """
    Retrospectively re-simulates historical trades.
    
    SAFE MODE: Only deletes PENDING results for re-simulation.
    Finalized results (PROFIT/LOSS/FLAT) from the live tracker are
    NEVER deleted — they are the source of truth for same-day trades.
    
    If force=true query param is passed, ALL results are deleted and
    re-simulated (use only when you're sure historical candle data is
    available, i.e. NOT on the same trading day after market close).
    """
    logger.info("Admin recovery: starting retrospective backtest...")
    try:
        # Get all unique dates from the Alert table where verdict is 'ENTER'
        dates = db.query(Alert.trigger_date).filter(Alert.verdict == "ENTER").distinct().all()
        dates = [d[0] for d in dates if d[0]]
        dates.sort()
        
        logger.info(f"Admin recovery: Found unique dates: {dates}")
        
        # Only delete PENDING results — preserve finalized trades from live tracker
        pending_deleted = db.query(BacktestResult).filter(
            BacktestResult.outcome == "PENDING"
        ).delete()
        db.commit()
        logger.info(f"Admin recovery: Deleted {pending_deleted} PENDING backtest results.")
        
        results = {}
        for date_str in dates:
            res = run_backtest(date_str)
            results[date_str] = {
                "win_rate": res.get("win_rate"),
                "net_pnl_amount": res.get("net_pnl_amount")
            }
        
        return {"status": "success", "processed_dates": dates, "details": results}
    except Exception as e:
        logger.error(f"Admin recovery failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/force-recovery")
def force_recovery(date: str = Query(None), db: Session = Depends(get_db)):
    """
    Force-delete ALL backtest results for a specific date and re-simulate.
    Only use this for PAST dates where Upstox historical candle data is
    guaranteed to be available (i.e., not the current trading day after hours).
    """
    if not date:
        raise HTTPException(status_code=400, detail="date query param is required (YYYY-MM-DD)")
    
    logger.info(f"Force recovery for date: {date}")
    try:
        # Delete only this date's results
        deleted = db.query(BacktestResult).join(Alert).filter(
            Alert.trigger_date == date
        ).delete(synchronize_session="fetch")
        db.commit()
        logger.info(f"Force recovery: Deleted {deleted} results for {date}")
        
        res = run_backtest(date)
        return {"status": "success", "date": date, "deleted": deleted, "result": res}
    except Exception as e:
        logger.error(f"Force recovery failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/restore-missing")
def restore_missing_records(date: str = Query(None), db: Session = Depends(get_db)):
    """
    Creates PENDING backtest records for any ENTER alerts that don't have one.
    Use this after a destructive recovery wiped data that can't be re-simulated
    (e.g., same-day candle data not available after market close).
    """
    query_date = date or datetime.now(IST).strftime("%Y-%m-%d")
    
    alerts = db.query(Alert).filter(
        Alert.trigger_date == query_date,
        Alert.verdict == "ENTER"
    ).all()
    
    created = 0
    for alert in alerts:
        existing = db.query(BacktestResult).filter(BacktestResult.alert_id == alert.id).first()
        if not existing:
            from backtest import create_pending_trade
            create_pending_trade(alert, db)
            created += 1
    
    return {"status": "success", "date": query_date, "alerts_found": len(alerts), "records_created": created}


@app.post("/api/admin/fix-inverted-sl")
def fix_inverted_sl(date: str = Query(None), db: Session = Depends(get_db)):
    """
    Scan all ENTER alerts for a date where stop_loss >= entry_price (inverted/corrupt SL).
    Clamps SL to entry * 0.995 (0.5% below entry).
    Also corrects any existing BacktestResult records that used the bad SL.
    """
    query_date = date or datetime.now(IST).strftime("%Y-%m-%d")
    alerts = db.query(Alert).filter(
        Alert.trigger_date == query_date,
        Alert.verdict == "ENTER"
    ).all()

    fixed = []
    for alert in alerts:
        entry = alert.entry_price
        sl = alert.stop_loss
        if entry and sl and sl >= entry:
            corrected_sl = round(entry * 0.995, 2)
            logger.warning(
                f"fix-inverted-sl: {alert.stock} SL={sl} >= Entry={entry}. "
                f"Clamping to {corrected_sl}"
            )
            alert.stop_loss = corrected_sl
            fixed.append({"stock": alert.stock, "old_sl": sl, "new_sl": corrected_sl, "entry": entry})

    if fixed:
        db.commit()
        logger.info(f"fix-inverted-sl: Corrected {len(fixed)} alerts for {query_date}")

    return {"status": "success", "date": query_date, "fixed_count": len(fixed), "details": fixed}


@app.post("/api/admin/eod-finalize")
def eod_finalize(date: str = Query(None), db: Session = Depends(get_db)):
    """
    Force-finalize all PENDING trades for a date using the EOD candle-based backtest.
    
    This works even AFTER market close because it uses historical 5-min candle data
    (which Upstox makes available from the NEXT trading day onwards).
    
    Call this the MORNING AFTER a trading day to finalize any trades that were
    PENDING due to Render sleeping during market hours.
    
    Workflow:
      1. First run /api/admin/fix-inverted-sl?date=YYYY-MM-DD to clean up bad SL values
      2. Then run /api/admin/eod-finalize?date=YYYY-MM-DD to simulate the day's trades
    """
    from backtest import run_backtest
    query_date = date or datetime.now(IST).strftime("%Y-%m-%d")

    # Count pending before
    pending_before = db.query(BacktestResult).join(Alert).filter(
        Alert.trigger_date == query_date,
        BacktestResult.outcome == "PENDING"
    ).count()

    logger.info(f"EOD finalize for {query_date}: {pending_before} PENDING trades")

    try:
        result = run_backtest(query_date)
        # Refresh pending count
        db.expire_all()
        pending_after = db.query(BacktestResult).join(Alert).filter(
            Alert.trigger_date == query_date,
            BacktestResult.outcome == "PENDING"
        ).count()
        return {
            "status": "success",
            "date": query_date,
            "pending_before": pending_before,
            "pending_after": pending_after,
            "backtest_result": result
        }
    except Exception as e:
        logger.error(f"EOD finalize failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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


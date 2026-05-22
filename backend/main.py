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
from datetime import datetime
from contextlib import asynccontextmanager
from collections import deque

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
    scheduler.start()
    logger.info("APScheduler started — Live tracking active. EOD backtest at 15:30.")
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

                alert = Alert(
                    scan_name     = scan_name,
                    alert_name    = alert_name,
                    stock         = stock,
                    trigger_time  = trigger_time,
                    trigger_date  = trigger_date,
                    trigger_price = trigger_price,
                    entry_price   = result["entry"],
                    target        = result["target"],
                    stop_loss     = result["stop_loss"],
                    vwap          = result["vwap"],
                    ema9          = result["ema9"],
                    pivot_r1      = result["pivot_r1"],
                    pivot_r2      = result["pivot_r2"],
                    pivot_s1      = result["pivot_s1"],
                    upper_wick    = result["upper_wick"],
                    solid_body    = result["solid_body"],
                    nifty_ltp     = result["nifty_ltp"],
                    nifty_vwap    = result["nifty_vwap"],
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

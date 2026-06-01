"""
main.py — FastAPI Application Entry Point (Simplified SQL Persistence Version)

Simplifies the platform:
- Single DB persistence (SQLAlchemy models)
- Webhook alerts evaluation and storage
- WebSocket updates for live price updates & alerts
- Restoring active monitoring tasks on server restart
"""

import os
import logging
import asyncio
import pytz
from datetime import datetime, date, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from upstox_client import upstox_get_candles, upstox_get_ltp
from rule_engine import evaluate_rules, calculate_vwap, calculate_pivot, calculate_resistances
from database import get_db, init_db, Alert, BacktestResult, SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ============ GLOBAL IN-MEMORY STATE ============
active_trades = {}      # {"BHARTIARTL": {entry_price, target, stop_loss, entered_at, status, ltp, pnl_pct}, ...}
exit_results = []       # [{"stock": "...", "pct": 0.48, "hit": "TARGET", "exit_price": ..., "exit_time": ...}, ...]
nifty_cache = None
nifty_cache_time = None

# ============ WEBSOCKET MANAGER ============
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Total active: {len(self.active_connections)}")
        # Send initial state immediately
        try:
            db = SessionLocal()
            try:
                today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
                alerts = db.query(Alert).filter(Alert.trigger_date == today_str).order_by(Alert.id.desc()).all()
                alerts_history = [{
                    "stock": a.stock,
                    "trigger_price": a.trigger_price,
                    "trigger_time": a.trigger_time,
                    "verdict": a.verdict,
                    "reason": a.verdict_reason,
                    "entry_price": a.entry_price,
                    "target": a.target,
                    "stop_loss": a.stop_loss
                } for a in alerts]
            finally:
                db.close()

            await websocket.send_json({
                "type": "state",
                "active_trades": active_trades,
                "exit_results": exit_results,
                "alerts_history": alerts_history
            })
        except Exception as e:
            logger.error(f"Error sending initial state: {e}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Total active: {len(self.active_connections)}")

    async def broadcast_json(self, data: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception:
                pass

manager = ConnectionManager()

async def state_broadcast_loop():
    """Broadcasts active trades, exit results, and NIFTY status to all clients every second."""
    while True:
        try:
            await manager.broadcast_json({
                "type": "state",
                "active_trades": active_trades,
                "exit_results": exit_results,
                "nifty_status": nifty_cache
            })
        except Exception as e:
            logger.error(f"Error in state broadcast loop: {e}")
        await asyncio.sleep(1)

async def nifty_refresh_loop():
    """Periodically refreshes NIFTY status in the background."""
    logger.info("Starting background NIFTY status refresh loop...")
    while True:
        try:
            await get_nifty_status()
        except Exception as e:
            logger.error(f"Error in NIFTY refresh loop: {e}")
        
        # If the cache is still empty, retry quickly (every 10s); otherwise refresh every 60s
        if nifty_cache is None:
            await asyncio.sleep(10)
        else:
            await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB
    init_db()

    db = SessionLocal()
    try:
        today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")

        # 1. Clean up/square off older pending trades from previous days
        older_pending = db.query(BacktestResult).join(Alert).filter(
            BacktestResult.outcome == "PENDING",
            Alert.trigger_date < today_str
        ).all()
        if older_pending:
            logger.info(f"Found {len(older_pending)} stale pending trades from previous days. Auto-squaring off...")
            for op in older_pending:
                alert = op.alert
                exit_p = alert.stop_loss if alert.stop_loss is not None else alert.entry_price
                op.exit_price = exit_p
                op.exit_time = "03:15 pm"
                op.outcome = "LOSS" if (alert.stop_loss is not None and alert.stop_loss < alert.entry_price) else "FLAT"
                entry_val = alert.entry_price or 1.0
                op.pnl_pct = round(((exit_p - entry_val) / entry_val) * 100, 2)
            db.commit()
            logger.info(f"Auto-squared off {len(older_pending)} stale pending trades.")

        # 2. Restore active trades from DB for TODAY only (where outcome is PENDING)
        pending_exits = db.query(BacktestResult).join(Alert).filter(
            BacktestResult.outcome == "PENDING",
            Alert.trigger_date == today_str
        ).all()
        for exit_res in pending_exits:
            alert = exit_res.alert
            if alert:
                active_trades[alert.stock] = {
                    "stock": alert.stock,
                    "entry_price": alert.entry_price,
                    "target": alert.target,
                    "stop_loss": alert.stop_loss,
                    "entered_at": alert.trigger_time,
                    "status": "monitoring",
                    "ltp": alert.entry_price,
                    "pnl_pct": 0.0
                }
                asyncio.create_task(monitor_trade(alert.stock, alert.target, alert.stop_loss))
                logger.info(f"Restored active trade monitor task for {alert.stock} | Target: {alert.target} | SL: {alert.stop_loss}")
        
        # 3. Restore today's closed exits to memory
        today_exits = db.query(BacktestResult).join(Alert).filter(
            Alert.trigger_date == today_str,
            BacktestResult.outcome != "PENDING"
        ).all()
        for ex in today_exits:
            exit_results.append({
                "stock": ex.alert.stock,
                "pct": ex.pnl_pct,
                "hit": "TARGET" if ex.outcome == "PROFIT" else "SL",
                "exit_price": ex.exit_price,
                "exit_time": ex.exit_time
            })
        logger.info(f"Loaded {len(exit_results)} exits for date {today_str} from DB.")
    except Exception as e:
        logger.error(f"Error restoring DB state on lifespan startup: {e}")
    finally:
        db.close()

    # Start background tasks
    broadcast_task = asyncio.create_task(state_broadcast_loop())
    nifty_task = asyncio.create_task(nifty_refresh_loop())
    logger.info("App startup: Started background WebSocket broadcaster and NIFTY refresher.")
    yield
    broadcast_task.cancel()
    nifty_task.cancel()
    logger.info("App shutdown: Stopped background tasks.")

# ============ FASTAPI APP ============
app = FastAPI(
    title="Trading Rule Engine - Simplified",
    description="In-memory Webhook Alerts and Real-Time WebSocket Monitoring",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ ENDPOINTS ============

@app.post("/api/webhook/chartink")
async def receive_chartink_alert(payload: dict):
    """
    Receives alerts from Chartink.
    Payload: {stocks, trigger_prices, triggered_at, scan_name, alert_name}
    """
    logger.info(f"Received Chartink alert: {payload}")
    stocks_raw = payload.get("stocks", "")
    prices_raw = payload.get("trigger_prices", "")
    timestamp_str = payload.get("triggered_at")  # e.g., "2:34 pm"
    
    stocks = [s.strip().upper() for s in stocks_raw.split(",") if s.strip()]
    prices = [p.strip() for p in (prices_raw or "").split(",") if p.strip()]
    
    for i, stock in enumerate(stocks):
        try:
            trigger_price = float(prices[i]) if i < len(prices) else 0.0
        except (ValueError, IndexError):
            trigger_price = 0.0
            
        # Process asynchronously in background
        asyncio.create_task(
            evaluate_stock(stock, trigger_price, timestamp_str)
        )
        
    return {"status": "received", "count": len(stocks)}

@app.websocket("/ws/prices")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for sending live updates and receiving keepalive."""
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket endpoint error: {e}")
    finally:
        manager.disconnect(websocket)

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "time": datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()}

@app.get("/api/alerts")
async def get_alerts_endpoint(
    date: str = Query(..., description="Date format: YYYY-MM-DD")
):
    db = SessionLocal()
    try:
        alerts = db.query(Alert).filter(Alert.trigger_date == date).order_by(Alert.id.desc()).all()
        return [{
            "stock": a.stock,
            "trigger_price": a.trigger_price,
            "trigger_time": a.trigger_time,
            "verdict": a.verdict,
            "reason": a.verdict_reason,
            "entry_price": a.entry_price,
            "target": a.target,
            "stop_loss": a.stop_loss
        } for a in alerts]
    except Exception as e:
        logger.error(f"Error fetching alerts: {e}")
        return []
    finally:
        db.close()

@app.get("/api/backtest-results")
async def get_backtest_results_endpoint(
    date: str = Query(..., description="Date format: YYYY-MM-DD")
):
    db = SessionLocal()
    try:
        exits = db.query(BacktestResult).join(Alert).filter(
            Alert.trigger_date == date,
            BacktestResult.outcome != "PENDING"
        ).order_by(BacktestResult.id.desc()).all()
        return [{
            "stock": ex.alert.stock,
            "pct": ex.pnl_pct,
            "hit": "TARGET" if ex.outcome == "PROFIT" else "SL",
            "exit_price": ex.exit_price,
            "exit_time": ex.exit_time
        } for ex in exits]
    except Exception as e:
        logger.error(f"Error fetching exits: {e}")
        return []
    finally:
        db.close()

# ============ CORE LOGIC ============

async def evaluate_stock(stock_symbol: str, trigger_price: float, timestamp_str: str | None):
    """
    1. Fetch live candles
    2. Evaluate 4 rules
    3. If ENTER: add to active_trades, save to DB, and start monitoring
    """
    db = SessionLocal()
    try:
        logger.info(f"Evaluating {stock_symbol} triggered at {timestamp_str}...")
        
        trigger_time = parse_time_to_ist(timestamp_str)
        trigger_date_str = trigger_time.strftime("%Y-%m-%d")
        
        # Check market hours
        if not is_market_hours(trigger_time):
            logger.warning(f"Alert outside market hours: {stock_symbol} at {trigger_time}. Skipping.")
            return

        # Fetch historical candles for this stock
        candles = await upstox_get_candles(stock_symbol)
        if not candles:
            logger.error(f"Failed to fetch candles for {stock_symbol}")
            return

        # ── Bug-2 Fix: Separate closed candles from the live tick ──────────────
        # get_candle_at_time() returns the last CLOSED candle whose timestamp
        # is <= trigger_time.  Any candle after that is still forming.
        current_candle = get_candle_at_time(candles, trigger_time)
        if not current_candle:
            logger.error(f"No matching candle at trigger time for {stock_symbol}")
            return

        idx = candles.index(current_candle)
        # closed_candles includes current_candle (the last fully closed 5-min bar)
        # but does NOT include any candle opened after trigger_time.
        closed_candles = candles[:idx + 1]

        # Fetch live LTP separately — this is the real-time tick *inside* the
        # currently forming (incomplete) candle and must not pollute VWAP/wick.
        live_ltp = await upstox_get_ltp(stock_symbol)
        if live_ltp is None:
            # Fall back to the last closed candle's close price
            live_ltp = current_candle["close"]
            logger.warning(f"Could not fetch live LTP for {stock_symbol}; using last closed candle close.")
        else:
            logger.info(f"Live LTP for {stock_symbol}: {live_ltp:.2f} (closed candle close: {current_candle['close']:.2f})")
        # ──────────────────────────────────────────────────────────────────────

        # Fetch NIFTY status (cached)
        nifty_status = await get_nifty_status()

        # Evaluate rules — pass closed_candles for structure, live_ltp for distance
        verdict, reason = evaluate_rules(
            stock=stock_symbol,
            current_candle=current_candle,
            closed_candles=closed_candles,
            nifty_status=nifty_status,
            trigger_time=trigger_time,
            live_price=live_ltp
        )
        
        # Calculate indicator statistics for DB logging (always from closed candles)
        vwap_val = calculate_vwap(closed_candles)
        pivot_val = calculate_pivot(closed_candles)
        r1, r2, r3 = calculate_resistances(pivot_val)
        
        closes = [c["close"] for c in closed_candles]
        ema9_val = sum(closes[-9:]) / len(closes[-9:]) if closes else 0.0
        
        upper_wick_val = current_candle["high"] - max(current_candle["open"], current_candle["close"])
        solid_body_val = abs(current_candle["open"] - current_candle["close"])
        
        # 1. Create Alert Record
        alert_rec = Alert(
            scan_name=None,
            alert_name=None,
            stock=stock_symbol,
            trigger_time=timestamp_str or trigger_time.strftime("%I:%M %p"),
            trigger_date=trigger_date_str,
            trigger_price=trigger_price,
            entry_price=round(current_candle["open"], 2),
            target=round(r1 if r1 > current_candle["open"] else r2, 2),
            stop_loss=round(min(vwap_val, current_candle["low"]), 2),
            vwap=round(vwap_val, 2),
            ema9=round(ema9_val, 2),
            pivot_r1=round(r1, 2),
            pivot_r2=round(r2, 2),
            pivot_r3=round(r3, 2),
            pivot_s1=round(pivot_val["pivot"], 2) if pivot_val else None,
            upper_wick=round(upper_wick_val, 2),
            solid_body=round(solid_body_val, 2),
            nifty_ltp=round(nifty_status["ltp"], 2) if nifty_status else None,
            nifty_vwap=round(nifty_status["vwap"], 2) if nifty_status else None,
            verdict=verdict,
            verdict_reason=reason
        )

        entry_price = current_candle["open"]
        sl = min(vwap_val, current_candle["low"])
        
        # Apply SL safety checks
        if sl < entry_price * 0.98:
            sl = entry_price * 0.99
        if sl >= entry_price:
            sl = entry_price * 0.995
            
        target = r1 if r1 > entry_price else (r2 if r2 > entry_price else (r3 if r3 > entry_price else entry_price * 1.015))
        
        alert_rec.entry_price = round(entry_price, 2)
        alert_rec.stop_loss = round(sl, 2)
        alert_rec.target = round(target, 2)
        
        db.add(alert_rec)
        db.commit()
        db.refresh(alert_rec)

        alert_data = {
            "stock": stock_symbol,
            "trigger_price": trigger_price,
            "trigger_time": timestamp_str or trigger_time.strftime("%I:%M %p"),
            "verdict": verdict,
            "reason": reason,
            "entry_price": round(entry_price, 2),
            "target": round(target, 2),
            "stop_loss": round(sl, 2)
        }
        
        # Broadcast alert immediately
        broadcast_alert(alert_data)
        logger.info(f"Verdict for {stock_symbol}: {verdict} | Reason: {reason} | DB Alert ID: {alert_rec.id}")
        
        # If ENTER, calculate entry/target/SL and start monitoring
        if verdict == "ENTER":
            trade_data = {
                "stock": stock_symbol,
                "entry_price": round(entry_price, 2),
                "target": round(target, 2),
                "stop_loss": round(sl, 2),
                "entered_at": timestamp_str or trigger_time.strftime("%I:%M %p"),
                "status": "monitoring",
                "ltp": round(entry_price, 2),
                "pnl_pct": 0.0
            }
            
            active_trades[stock_symbol] = trade_data
            
            # Create BacktestResult DB record with outcome 'PENDING'
            exit_rec = BacktestResult(
                alert_id=alert_rec.id,
                entry_time=timestamp_str or trigger_time.strftime("%I:%M %p"),
                entry_price=round(entry_price, 2),
                exit_time=None,
                exit_price=None,
                outcome="PENDING",
                pnl_pct=0.0,
                quantity=1,
                pnl_amount=0.0
            )
            db.add(exit_rec)
            db.commit()
            
            # Start monitoring in background
            asyncio.create_task(monitor_trade(stock_symbol, target, sl))
            logger.info(f"Entered active trade for {stock_symbol} @ {entry_price:.2f} | Target: {target:.2f} | SL: {sl:.2f}")

    except Exception as e:
        logger.error(f"Error evaluating {stock_symbol}: {e}", exc_info=True)
    finally:
        db.close()

async def monitor_trade(stock_symbol: str, target: float, stop_loss: float):
    """
    Every 10 seconds, check if target or SL is hit.
    """
    logger.info(f"Starting background monitoring task for {stock_symbol}...")
    while stock_symbol in active_trades:
        try:
            current_price = await upstox_get_ltp(stock_symbol)
            if current_price is None:
                await asyncio.sleep(10)
                continue
            
            trade = active_trades[stock_symbol]
            entry_price = trade["entry_price"]
            trade["ltp"] = current_price
            trade["pnl_pct"] = round(((current_price - entry_price) / entry_price) * 100, 2)
            
            # Check target hit
            if current_price >= target:
                pct = ((target - entry_price) / entry_price) * 100
                exit_time_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%I:%M %p")
                
                # Update database
                db = SessionLocal()
                try:
                    today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
                    exit_rec = db.query(BacktestResult).join(Alert).filter(
                        Alert.stock == stock_symbol,
                        Alert.trigger_date == today_str,
                        BacktestResult.outcome == "PENDING"
                    ).first()
                    if exit_rec:
                        exit_rec.exit_price = round(target, 2)
                        exit_rec.exit_time = exit_time_str
                        exit_rec.outcome = "PROFIT"
                        exit_rec.pnl_pct = round(pct, 2)
                        db.commit()
                        logger.info(f"Updated DB backtest result for {stock_symbol} to PROFIT.")
                except Exception as ex:
                    logger.error(f"Error updating DB for target hit: {ex}")
                finally:
                    db.close()
                    
                exit_results.append({
                    "stock": stock_symbol,
                    "pct": round(pct, 2),
                    "hit": "TARGET",
                    "exit_price": round(target, 2),
                    "exit_time": exit_time_str
                })
                logger.info(f"Target hit for {stock_symbol} at {current_price:.2f}. Trade closed.")
                del active_trades[stock_symbol]
                break
                
            # Check stop loss hit
            if current_price <= stop_loss:
                pct = ((stop_loss - entry_price) / entry_price) * 100
                exit_time_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%I:%M %p")
                
                # Update database
                db = SessionLocal()
                try:
                    today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
                    exit_rec = db.query(BacktestResult).join(Alert).filter(
                        Alert.stock == stock_symbol,
                        Alert.trigger_date == today_str,
                        BacktestResult.outcome == "PENDING"
                    ).first()
                    if exit_rec:
                        exit_rec.exit_price = round(stop_loss, 2)
                        exit_rec.exit_time = exit_time_str
                        exit_rec.outcome = "LOSS"
                        exit_rec.pnl_pct = round(pct, 2)
                        db.commit()
                        logger.info(f"Updated DB backtest result for {stock_symbol} to LOSS.")
                except Exception as ex:
                    logger.error(f"Error updating DB for SL hit: {ex}")
                finally:
                    db.close()
                    
                exit_results.append({
                    "stock": stock_symbol,
                    "pct": round(pct, 2),
                    "hit": "SL",
                    "exit_price": round(stop_loss, 2),
                    "exit_time": exit_time_str
                })
                logger.info(f"Stop Loss hit for {stock_symbol} at {current_price:.2f}. Trade closed.")
                del active_trades[stock_symbol]
                break
                
            await asyncio.sleep(10)
            
        except Exception as e:
            logger.error(f"Monitoring error for {stock_symbol}: {e}")
            await asyncio.sleep(10)

async def get_nifty_status():
    """
    Fetch NIFTY VWAP with 60-second cache.
    """
    global nifty_cache, nifty_cache_time
    
    now = datetime.now()
    if nifty_cache and nifty_cache_time and (now - nifty_cache_time).total_seconds() < 60:
        return nifty_cache
        
    try:
        nifty_candles = await upstox_get_candles("NIFTY50")
        if not nifty_candles:
            logger.warning("No NIFTY50 candles returned from Upstox API.")
            return nifty_cache  # return stale cache if API call fails

        # ── Bug-2 Fix: Use real LTP for NIFTY, not last candle close ──────────
        # The last candle in the list may be an incomplete (still-forming) candle.
        # We fetch the live LTP from the API so the bullish/bearish determination
        # reflects the actual current market tick, not a stale partial-candle close.
        nifty_ltp_live = await upstox_get_ltp("NIFTY50")

        # VWAP is computed on ALL closed candles for today only (daily anchor).
        # We intentionally skip slicing to [-50:] here; calculate_vwap already
        # filters to today's candles, so passing all candles is correct.
        nifty_vwap = calculate_vwap(nifty_candles)

        # Prefer live LTP; fall back to last closed candle's close if unavailable
        nifty_ltp = nifty_ltp_live if nifty_ltp_live is not None else nifty_candles[-1]["close"]
        if nifty_ltp_live is None:
            logger.warning("Could not fetch live NIFTY50 LTP; using last candle close as fallback.")
        # ──────────────────────────────────────────────────────────────────────

        nifty_cache = {
            "ltp": nifty_ltp,
            "vwap": nifty_vwap,
            "is_bullish": nifty_ltp >= nifty_vwap
        }
        nifty_cache_time = now
        logger.info(f"Polled NIFTY50: Live LTP={nifty_ltp:.2f} | VWAP={nifty_vwap:.2f} | Bullish={nifty_ltp >= nifty_vwap}")
        return nifty_cache
    except Exception as e:
        logger.error(f"Error getting nifty status: {e}", exc_info=True)
        return nifty_cache

def broadcast_alert(alert_data: dict):
    """Send alert to all connected WebSocket clients."""
    asyncio.create_task(manager.broadcast_json({
        "type": "alert",
        "alert": alert_data
    }))

# ============ HELPER FUNCTIONS ============

def parse_time_to_ist(time_str: str | None) -> datetime:
    """Convert string like '2:34 pm' to naive IST datetime for today."""
    IST = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(IST)
    
    if not time_str:
        return now_ist.replace(tzinfo=None)
        
    time_str = time_str.strip().lower()
    parsed_time = None
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M:%S", "%H:%M"):
        try:
            parsed_time = datetime.strptime(time_str, fmt).time()
            break
        except ValueError:
            continue
            
    if parsed_time is None:
        return now_ist.replace(tzinfo=None)
        
    # Combine with today's date in IST
    return datetime.combine(now_ist.date(), parsed_time)

def is_market_hours(dt: datetime) -> bool:
    """Check if datetime is within Mon-Fri 09:15-15:30 IST."""
    if dt.weekday() >= 5:
        return False
    return time(9, 15) <= dt.time() <= time(15, 30)

def get_candle_at_time(candles: list[dict], target_time: datetime) -> dict | None:
    """Find closest 5-min candle to target_time (timestamp <= target_time)."""
    matching = [c for c in candles if c["timestamp"] <= target_time]
    if matching:
        return matching[-1]
    return candles[-1] if candles else None

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

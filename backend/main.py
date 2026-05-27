"""
main.py — FastAPI Application Entry Point (Revamped & Refactored)

Simplifies the platform:
- In-memory state (no DB persistence)
- Webhook alerts evaluation
- WebSocket updates for live price updates & alerts
"""

import logging
import asyncio
import json
import pytz
from datetime import datetime, date, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from upstox_client import upstox_get_candles, upstox_get_ltp
from rule_engine import evaluate_rules, calculate_vwap, calculate_pivot, calculate_resistances

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

import os

# ============ GLOBAL IN-MEMORY STATE ============
active_trades = {}      # {"BHARTIARTL": {entry_price, target, stop_loss, entered_at, status, ltp, pnl_pct}, ...}
exit_results = []       # [{"stock": "...", "pct": 0.48, "hit": "TARGET", "exit_price": ..., "exit_time": ...}, ...]
alerts_history = []     # [{"stock": "...", "trigger_price": ..., "verdict": ..., "reason": ...}, ...]
nifty_cache = None
nifty_cache_time = None

STATE_FILE = "state.json"

def save_state():
    try:
        state = {
            "active_trades": active_trades,
            "exit_results": exit_results,
            "alerts_history": alerts_history
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, default=str)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")

def load_state():
    global active_trades, exit_results, alerts_history
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                active_trades = state.get("active_trades", {})
                exit_results = state.get("exit_results", [])
                alerts_history = state.get("alerts_history", [])
            logger.info(f"Loaded state: {len(active_trades)} active trades, {len(exit_results)} exits, {len(alerts_history)} alerts.")
    except Exception as e:
        logger.error(f"Failed to load state: {e}")

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
            await websocket.send_json({
                "type": "state",
                "active_trades": active_trades,
                "exit_results": exit_results,
                "alerts_history": alerts_history
            })
        except Exception:
            pass

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
                "nifty_status": nifty_cache,
                "alerts_history": alerts_history
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
    # Load state from file
    load_state()

    # Restore active trade monitoring tasks
    for symbol, trade in list(active_trades.items()):
        try:
            target = float(trade["target"])
            sl = float(trade["stop_loss"])
            asyncio.create_task(monitor_trade(symbol, target, sl))
            logger.info(f"Restored active trade monitor task for {symbol} | Target: {target} | SL: {sl}")
        except Exception as e:
            logger.error(f"Failed to restore active trade monitor task for {symbol}: {e}")

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
            # Wait for any message from client (e.g., keepalive ping)
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

# ============ CORE LOGIC ============

async def evaluate_stock(stock_symbol: str, trigger_price: float, timestamp_str: str | None):
    """
    1. Fetch live candles
    2. Evaluate 4 rules
    3. If ENTER: add to active_trades and start monitoring
    """
    try:
        logger.info(f"Evaluating {stock_symbol} triggered at {timestamp_str}...")
        
        trigger_time = parse_time_to_ist(timestamp_str)
        
        # Check market hours
        if not is_market_hours(trigger_time):
            logger.warning(f"Alert outside market hours: {stock_symbol} at {trigger_time}. Skipping.")
            return

        # Fetch historical candles for this stock
        candles = await upstox_get_candles(stock_symbol)
        if not candles:
            logger.error(f"Failed to fetch candles for {stock_symbol}")
            return

        # Get current 5-min candle closest to trigger_time
        current_candle = get_candle_at_time(candles, trigger_time)
        if not current_candle:
            logger.error(f"No matching candle at trigger time for {stock_symbol}")
            return

        # Fetch NIFTY status (cached)
        nifty_status = await get_nifty_status()

        # Evaluate rules
        idx = candles.index(current_candle)
        historical_candles = candles[:idx+1]

        verdict, reason = evaluate_rules(
            stock=stock_symbol,
            current_candle=current_candle,
            all_candles=historical_candles,
            nifty_status=nifty_status,
            trigger_time=trigger_time
        )
        
        alert_data = {
            "stock": stock_symbol,
            "trigger_price": trigger_price,
            "trigger_time": timestamp_str,
            "verdict": verdict,
            "reason": reason
        }
        
        # Save to alerts_history
        alerts_history.insert(0, alert_data)
        if len(alerts_history) > 100:
            alerts_history.pop()
        save_state()

        # Broadcast alert immediately
        broadcast_alert(alert_data)
        logger.info(f"Verdict for {stock_symbol}: {verdict} | Reason: {reason}")
        
        # If ENTER, calculate entry/target/SL and start monitoring
        if verdict == "ENTER":
            entry_price = current_candle["open"]
            
            # Calculate indicators up to current candle
            idx = candles.index(current_candle)
            vwap = calculate_vwap(candles[:idx+1])
            pivot = calculate_pivot(candles[:idx+1])
            r1, r2, r3 = calculate_resistances(pivot)
            
            sl = min(vwap, current_candle["low"])
            
            # Apply SL safety checks (from previous fixes)
            if sl < entry_price * 0.98:
                sl = entry_price * 0.99
            if sl >= entry_price:
                sl = entry_price * 0.995
                
            # Target is next resistance above entry, breakout target is +1.5%
            target = r1 if r1 > entry_price else (r2 if r2 > entry_price else (r3 if r3 > entry_price else entry_price * 1.015))
            
            trade_data = {
                "stock": stock_symbol,
                "entry_price": round(entry_price, 2),
                "target": round(target, 2),
                "stop_loss": round(sl, 2),
                "entered_at": timestamp_str,
                "status": "monitoring",
                "ltp": round(entry_price, 2),
                "pnl_pct": 0.0
            }
            
            active_trades[stock_symbol] = trade_data
            save_state()
            
            # Start monitoring in background
            asyncio.create_task(monitor_trade(stock_symbol, target, sl))
            logger.info(f"Entered active trade for {stock_symbol} @ {entry_price:.2f} | Target: {target:.2f} | SL: {sl:.2f}")

    except Exception as e:
        logger.error(f"Error evaluating {stock_symbol}: {e}", exc_info=True)


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
                exit_results.append({
                    "stock": stock_symbol,
                    "pct": round(pct, 2),
                    "hit": "TARGET",
                    "exit_price": round(target, 2),
                    "exit_time": datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%I:%M %p")
                })
                logger.info(f"Target hit for {stock_symbol} at {current_price:.2f}. Trade closed.")
                del active_trades[stock_symbol]
                save_state()
                break
                
            # Check stop loss hit
            if current_price <= stop_loss:
                pct = ((stop_loss - entry_price) / entry_price) * 100
                exit_results.append({
                    "stock": stock_symbol,
                    "pct": round(pct, 2),
                    "hit": "SL",
                    "exit_price": round(stop_loss, 2),
                    "exit_time": datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%I:%M %p")
                })
                logger.info(f"Stop Loss hit for {stock_symbol} at {current_price:.2f}. Trade closed.")
                del active_trades[stock_symbol]
                save_state()
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
            
        nifty_vwap = calculate_vwap(nifty_candles[-50:])  # Last 50 candles
        nifty_ltp = nifty_candles[-1]["close"]
        
        nifty_cache = {
            "ltp": nifty_ltp,
            "vwap": nifty_vwap,
            "is_bullish": nifty_ltp >= nifty_vwap
        }
        nifty_cache_time = now
        logger.info(f"Polled NIFTY50 from Upstox: LTP={nifty_ltp:.2f} | VWAP={nifty_vwap:.2f} | Bullish={nifty_ltp >= nifty_vwap}")
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

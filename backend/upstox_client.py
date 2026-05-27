"""
upstox_client.py — Upstox API Wrapper (Refactored)

Uses the long-lived access token stored in UPSTOX_ACCESS_TOKEN env var.
Exposes both synchronous methods and async wrappers for FastAPI integration.
"""

import os
import json
import logging
import requests
import asyncio
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
UPSTOX_BASE_V2 = "https://api.upstox.com/v2"
UPSTOX_BASE_V3 = "https://api.upstox.com/v3"
NIFTY_KEY = "NSE_INDEX|Nifty 50"

# Instrument keys JSON file mapping
_INSTRUMENT_KEYS_FILE = Path(__file__).parent / "instrument_keys.json"
_instrument_keys: dict[str, str] = {}

def load_instrument_keys():
    global _instrument_keys
    if _INSTRUMENT_KEYS_FILE.exists():
        try:
            with open(_INSTRUMENT_KEYS_FILE, "r") as f:
                _instrument_keys = json.load(f)
            logger.info(f"Loaded {len(_instrument_keys)} instrument keys from {_INSTRUMENT_KEYS_FILE.name}.")
        except Exception as e:
            logger.error(f"Error loading instrument keys: {e}")
    else:
        logger.warning(f"instrument_keys.json not found at {_INSTRUMENT_KEYS_FILE.absolute()}")

# Load mapping on module import
load_instrument_keys()

# ── Auth ───────────────────────────────────────────────────────────────────────
def _get_token() -> str:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        raise ValueError("UPSTOX_ACCESS_TOKEN is not set.")
    return token

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Accept": "application/json",
    }

def get_instrument_key(symbol: str) -> str:
    """Look up instrument_key from JSON mapping."""
    symbol = symbol.upper().strip()
    if symbol in ("NIFTY50", "NIFTY", "NIFTY 50"):
        return NIFTY_KEY
    key = _instrument_keys.get(symbol)
    if not key:
        logger.warning(f"Could not resolve key for symbol: {symbol}. Fallback to generic.")
        # Fallback format if cache doesn't have it, though usually it should
        return f"NSE_EQ|{symbol}"
    return key

# ── Historical Data ────────────────────────────────────────────────────────────
def get_historical_data(symbol: str, days: int = 3, end_date_str: str | None = None) -> pd.DataFrame:
    """Fetch the last `days` of 5-minute OHLCV candles from Upstox V3."""
    instrument_key = get_instrument_key(symbol)

    if end_date_str:
        to_date_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
    else:
        to_date_dt = datetime.now()

    to_date   = to_date_dt.strftime("%Y-%m-%d")
    from_date = (to_date_dt - timedelta(days=days + 2)).strftime("%Y-%m-%d")

    url = (
        f"{UPSTOX_BASE_V3}/historical-candle"
        f"/{requests.utils.quote(instrument_key, safe='')}"
        f"/minutes/5/{to_date}/{from_date}"
    )

    resp = requests.get(url, headers=_headers(), timeout=15)
    resp.raise_for_status()

    data = resp.json()
    candles = data.get("data", {}).get("candles", [])

    if not candles:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume", "oi"]
    )
    # Parse timestamps to naive IST
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("timestamp").reset_index(drop=True)

    cutoff = (to_date_dt - timedelta(days=days + 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    df = df[df["timestamp"] >= pd.Timestamp(cutoff)]

    return df

# ── Live LTP ──────────────────────────────────────────────────────────────────
def get_ltp(symbol: str) -> float | None:
    """Fetch the Last Traded Price for a symbol."""
    try:
        instrument_key = get_instrument_key(symbol)

        url = f"{UPSTOX_BASE_V2}/market-quote/ltp"
        params = {"symbol": instrument_key}
        resp = requests.get(url, headers=_headers(), params=params, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        quotes = data.get("data", {})
        for key in quotes:
            ltp = quotes[key].get("last_price")
            if ltp is not None:
                return float(ltp)
    except Exception as e:
        logger.error(f"get_ltp({symbol}) failed: {e}")
    return None

# ── Async Wrappers ─────────────────────────────────────────────────────────────
async def upstox_get_candles(stock_symbol: str) -> list[dict]:
    """Fetch 5-min candles from Upstox (Async)."""
    try:
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, get_historical_data, stock_symbol, 3)
        if df.empty:
            return []
        
        # Convert timestamp to naive datetime objects
        df["timestamp"] = df["timestamp"].dt.to_pydatetime()
        return df.to_dict("records")
    except Exception as e:
        logger.error(f"upstox_get_candles for {stock_symbol} failed: {e}")
        return []

async def upstox_get_ltp(stock_symbol: str) -> float | None:
    """Fetch live LTP from Upstox (Async)."""
    try:
        loop = asyncio.get_running_loop()
        ltp = await loop.run_in_executor(None, get_ltp, stock_symbol)
        return ltp
    except Exception as e:
        logger.error(f"upstox_get_ltp for {stock_symbol} failed: {e}")
        return None

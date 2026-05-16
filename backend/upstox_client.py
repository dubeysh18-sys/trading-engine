"""
upstox_client.py — Upstox API V2/V3 wrapper

Uses the long-lived access token stored in UPSTOX_ACCESS_TOKEN env var.
No OAuth dance needed — token is valid for ~1 year.
"""

import os
import json
import logging
import requests
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

# Instrument master (cached on first call)
_instrument_cache: dict[str, str] = {}  # symbol -> instrument_key
_INSTRUMENT_CACHE_FILE = Path(__file__).parent / "instrument_cache.json"


# ── Auth ───────────────────────────────────────────────────────────────────────
def _get_token() -> str:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        raise ValueError(
            "UPSTOX_ACCESS_TOKEN is not set. "
            "Copy .env.template to .env and paste your token."
        )
    return token


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Accept": "application/json",
    }


# ── Instrument Key Resolution ──────────────────────────────────────────────────
def _load_instrument_cache():
    """Load cached symbol→key mapping from disk."""
    global _instrument_cache
    if _INSTRUMENT_CACHE_FILE.exists():
        with open(_INSTRUMENT_CACHE_FILE, "r") as f:
            _instrument_cache = json.load(f)
        logger.info(f"Loaded {len(_instrument_cache)} cached instrument keys.")


def _save_instrument_cache():
    with open(_INSTRUMENT_CACHE_FILE, "w") as f:
        json.dump(_instrument_cache, f)


def _fetch_instrument_master():
    """
    Download Upstox NSE_EQ instrument master and build a
    trading_symbol → instrument_key lookup dictionary.
    The master is ~3MB JSON so we cache it to disk.
    """
    global _instrument_cache
    logger.info("Downloading Upstox NSE_EQ instrument master...")
    url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    instruments = resp.json()
    new_cache = {}
    for instr in instruments:
        symbol = instr.get("trading_symbol", "")
        key    = instr.get("instrument_key", "")
        itype  = instr.get("instrument_type", "")
        segment = instr.get("segment", "")
        # Only equity cash segment
        if segment == "NSE_EQ" and itype == "EQ" and symbol and key:
            new_cache[symbol.upper()] = key

    _instrument_cache = new_cache
    _save_instrument_cache()
    logger.info(f"Instrument master loaded: {len(_instrument_cache)} equity symbols.")


def resolve_instrument_key(symbol: str) -> str:
    """
    Convert a plain NSE trading symbol (e.g. 'TCS', 'RELIANCE') to
    an Upstox instrument_key (e.g. 'NSE_EQ|INE040A01034').
    """
    symbol = symbol.upper().strip()

    # Load cache if empty
    if not _instrument_cache:
        _load_instrument_cache()

    # If still empty (first run), fetch the master
    if not _instrument_cache:
        _fetch_instrument_master()

    if symbol not in _instrument_cache:
        # Try refreshing master in case it's a new listing
        _fetch_instrument_master()

    key = _instrument_cache.get(symbol)
    if not key:
        raise ValueError(f"Could not resolve instrument key for symbol: {symbol}")
    return key


# ── Historical Data ────────────────────────────────────────────────────────────
def get_historical_data(symbol: str, days: int = 3) -> pd.DataFrame:
    """
    Fetch the last `days` of 5-minute OHLCV candles from Upstox V3.

    Returns a DataFrame with columns:
        timestamp, open, high, low, close, volume
    Sorted by timestamp ascending.
    """
    if symbol.upper() == "NIFTY50" or symbol.upper() == "NIFTY":
        instrument_key = NIFTY_KEY
    else:
        instrument_key = resolve_instrument_key(symbol)

    to_date   = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=days + 2)).strftime("%Y-%m-%d")
    # +2 to account for weekends

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
        logger.warning(f"No historical data returned for {symbol}")
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume", "oi"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Keep only last `days` trading days
    cutoff = (datetime.now() - timedelta(days=days + 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    df = df[df["timestamp"] >= pd.Timestamp(cutoff)]

    return df


# ── Live LTP ──────────────────────────────────────────────────────────────────
def get_ltp(symbol: str) -> float | None:
    """
    Fetch the Last Traded Price for a symbol.
    Returns float price or None on error.
    """
    try:
        if symbol.upper() in ("NIFTY50", "NIFTY", "NIFTY 50"):
            instrument_key = NIFTY_KEY
        else:
            instrument_key = resolve_instrument_key(symbol)

        url = f"{UPSTOX_BASE_V2}/market-quote/ltp"
        params = {"symbol": instrument_key}
        resp = requests.get(url, headers=_headers(), params=params, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        quotes = data.get("data", {})
        # Key format varies — try both formats
        for key in quotes:
            ltp = quotes[key].get("last_price")
            if ltp is not None:
                return float(ltp)
    except Exception as e:
        logger.error(f"get_ltp({symbol}) failed: {e}")
    return None


# ── Nifty Status ──────────────────────────────────────────────────────────────
def get_nifty_status(nifty_vwap: float) -> dict:
    """Return NIFTY LTP and whether market wind is bullish."""
    ltp = get_ltp("NIFTY50")
    if ltp is None:
        return {"ltp": None, "vwap": nifty_vwap, "is_bullish": None, "error": "LTP fetch failed"}
    return {
        "ltp": ltp,
        "vwap": nifty_vwap,
        "is_bullish": ltp >= nifty_vwap,
        "error": None,
    }

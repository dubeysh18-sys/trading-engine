"""
database.py — SQLAlchemy models and session management

Supports both:
  - PostgreSQL (production on Render) via DATABASE_URL = postgres://...
  - SQLite    (local dev)            via DATABASE_URL = sqlite:///./trading.db

CRITICAL: SQLite is ephemeral on Render's free tier — always use PostgreSQL in production.
"""

import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    DateTime, ForeignKey, Text, event
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./trading.db")

# Render and some providers return "postgres://" but SQLAlchemy 2.x requires "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_SQLITE = DATABASE_URL.startswith("sqlite")

# ── Engine ─────────────────────────────────────────────────────────────────────
if IS_SQLITE:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    # Enable WAL mode for better concurrent reads on SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,           # auto-reconnect on stale connections
        pool_recycle=300,             # recycle connections every 5 min
        connect_args={
            "sslmode": "require",     # Render PostgreSQL requires SSL
            "connect_timeout": 10,
        },
        echo=False,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── Models ─────────────────────────────────────────────────────────────────────

class Alert(Base):
    """Stores each stock alert received from Chartink after rule evaluation."""
    __tablename__ = "alerts"

    id            = Column(Integer, primary_key=True, index=True)
    scan_name     = Column(String(200), nullable=True)
    alert_name    = Column(String(200), nullable=True)
    stock         = Column(String(20),  nullable=False)
    trigger_time  = Column(String(20),  nullable=False)   # "2:34 pm"
    trigger_date  = Column(String(20),  nullable=False)   # "YYYY-MM-DD"

    trigger_price = Column(Float, nullable=True)
    entry_price   = Column(Float, nullable=True)
    target        = Column(Float, nullable=True)
    stop_loss     = Column(Float, nullable=True)
    vwap          = Column(Float, nullable=True)
    ema9          = Column(Float, nullable=True)
    pivot_r1      = Column(Float, nullable=True)
    pivot_r2      = Column(Float, nullable=True)
    pivot_r3      = Column(Float, nullable=True)   # Added
    pivot_s1      = Column(Float, nullable=True)
    upper_wick    = Column(Float, nullable=True)
    solid_body    = Column(Float, nullable=True)
    nifty_ltp     = Column(Float, nullable=True)
    nifty_vwap    = Column(Float, nullable=True)

    verdict        = Column(String(100), nullable=False, default="PENDING")
    verdict_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    backtest_result = relationship("BacktestResult", back_populates="alert", uselist=False)


class BacktestResult(Base):
    """Stores EOD backtest outcome for each ENTER alert."""
    __tablename__ = "backtest_results"

    id          = Column(Integer, primary_key=True, index=True)
    alert_id    = Column(Integer, ForeignKey("alerts.id", ondelete="CASCADE"), unique=True, nullable=False)

    entry_time  = Column(String(20),  nullable=True)
    entry_price = Column(Float,       nullable=True)
    exit_time   = Column(String(20),  nullable=True)
    exit_price  = Column(Float,       nullable=True)
    outcome     = Column(String(20),  nullable=True)   # PROFIT | LOSS | FLAT | PENDING
    pnl_pct     = Column(Float,       nullable=True)
    quantity    = Column(Integer,     nullable=True)
    pnl_amount  = Column(Float,       nullable=True)

    created_at  = Column(DateTime, default=datetime.utcnow)

    alert = relationship("Alert", back_populates="backtest_result")


# ── Session Dependency ────────────────────────────────────────────────────────

def get_db():
    """FastAPI dependency — yields a DB session and closes it when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Create all tables if they don't exist.
    Uses CREATE TABLE IF NOT EXISTS — safe to call on every startup,
    never drops data, never resets sequences.
    """
    Base.metadata.create_all(bind=engine)

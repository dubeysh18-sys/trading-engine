"""
database.py — SQLAlchemy models and session management
"""

import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    DateTime, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./trading.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Alert(Base):
    """Stores each stock alert received from Chartink after rule evaluation."""
    __tablename__ = "alerts"

    id           = Column(Integer, primary_key=True, index=True)
    scan_name    = Column(String(200), nullable=True)
    alert_name   = Column(String(200), nullable=True)
    stock        = Column(String(20), nullable=False)
    trigger_time = Column(String(20), nullable=False)   # "2:34 pm" as received
    trigger_date = Column(String(20), nullable=False)   # "YYYY-MM-DD"

    # Indicator snapshot at time of alert
    trigger_price = Column(Float, nullable=True)
    entry_price   = Column(Float, nullable=True)
    target        = Column(Float, nullable=True)
    stop_loss     = Column(Float, nullable=True)
    vwap          = Column(Float, nullable=True)
    ema9          = Column(Float, nullable=True)
    pivot_r1      = Column(Float, nullable=True)
    pivot_r2      = Column(Float, nullable=True)
    pivot_s1      = Column(Float, nullable=True)
    upper_wick    = Column(Float, nullable=True)
    solid_body    = Column(Float, nullable=True)
    nifty_ltp     = Column(Float, nullable=True)
    nifty_vwap    = Column(Float, nullable=True)

    verdict       = Column(String(100), nullable=False, default="PENDING")
    verdict_reason = Column(Text, nullable=True)

    created_at    = Column(DateTime, default=datetime.utcnow)

    # Relationship to backtest result
    backtest_result = relationship("BacktestResult", back_populates="alert", uselist=False)


class BacktestResult(Base):
    """Stores EOD backtest outcome for each ENTER alert."""
    __tablename__ = "backtest_results"

    id          = Column(Integer, primary_key=True, index=True)
    alert_id    = Column(Integer, ForeignKey("alerts.id"), unique=True, nullable=False)

    entry_time  = Column(String(20), nullable=True)
    entry_price = Column(Float, nullable=True)
    exit_time   = Column(String(20), nullable=True)
    exit_price  = Column(Float, nullable=True)
    outcome     = Column(String(20), nullable=True)   # PROFIT | LOSS | FLAT
    pnl_pct     = Column(Float, nullable=True)        # e.g. 1.25 means +1.25%

    created_at  = Column(DateTime, default=datetime.utcnow)

    alert = relationship("Alert", back_populates="backtest_result")


def get_db():
    """FastAPI dependency — yields a DB session and closes it when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables on startup."""
    Base.metadata.create_all(bind=engine)

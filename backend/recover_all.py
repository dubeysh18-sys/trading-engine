"""
recover_all.py — Retrospectively re-simulates all historical trades.
Deletes all old backtest results and re-runs the backtest simulation
for all unique alert dates in the database using the updated logic.
"""

import os
import logging
from database import SessionLocal, Alert, BacktestResult, init_db
from backtest import run_backtest

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def main():
    logger.info("Initializing database...")
    init_db()
    
    db = SessionLocal()
    try:
        logger.info("Fetching distinct alert dates...")
        # Get all unique dates from the Alert table where verdict is 'ENTER'
        dates = db.query(Alert.trigger_date).filter(Alert.verdict == "ENTER").distinct().all()
        dates = [d[0] for d in dates if d[0]]
        dates.sort()
        
        logger.info(f"Found {len(dates)} unique dates with ENTER alerts: {dates}")
        
        logger.info("Deleting all existing BacktestResult records to force full re-simulation...")
        deleted_count = db.query(BacktestResult).delete()
        db.commit()
        logger.info(f"Successfully deleted {deleted_count} old backtest results.")
        
        for date_str in dates:
            logger.info(f"Running backtest for date: {date_str}...")
            res = run_backtest(date_str)
            logger.info(f"Result for {date_str}: Win Rate: {res.get('win_rate')}% | Net P&L (₹): {res.get('net_pnl_amount')}")
            
        logger.info("Retrospective recovery complete!")
    except Exception as e:
        logger.error(f"Error during recovery: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()

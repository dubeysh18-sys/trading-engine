"""Quick check: does days=2 or days=3 bring back today's data?"""
import sys
sys.path.insert(0, "backend")
from upstox_client import get_historical_data

for d in [1, 2, 3, 5]:
    print(f"\n--- days={d}, end='2026-05-26' ---")
    df = get_historical_data("AFCONS", days=d, end_date_str="2026-05-26")
    if not df.empty:
        df["ds"] = df["timestamp"].dt.strftime("%Y-%m-%d")
        print(f"  Rows: {len(df)}, Dates: {sorted(df['ds'].unique())}")
    else:
        print("  EMPTY")

# Also test without end_date
for d in [1, 2, 3]:
    print(f"\n--- days={d}, no end_date (defaults to now) ---")
    df = get_historical_data("AFCONS", days=d)
    if not df.empty:
        df["ds"] = df["timestamp"].dt.strftime("%Y-%m-%d")
        print(f"  Rows: {len(df)}, Dates: {sorted(df['ds'].unique())}")
    else:
        print("  EMPTY")

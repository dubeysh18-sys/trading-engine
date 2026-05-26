"""
Quick test: What candles does Upstox return for different to_date values?
This will reveal if to_date is inclusive or exclusive.
"""
import sys
sys.path.insert(0, "backend")

from upstox_client import get_historical_data
import pandas as pd

# Test 1: end_date_str = "2026-05-26" (today) with days=1
print("=" * 60)
print("TEST 1: get_historical_data('AFCONS', days=1, end_date_str='2026-05-26')")
print("=" * 60)
df = get_historical_data("AFCONS", days=1, end_date_str="2026-05-26")
print(f"Total rows: {len(df)}")
if not df.empty:
    print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    # Show unique dates
    df["date_str"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    print(f"Unique dates in data: {sorted(df['date_str'].unique())}")
    # Show first and last few rows
    print("\nFirst 3 rows:")
    print(df.head(3).to_string())
    print("\nLast 3 rows:")
    print(df.tail(3).to_string())
else:
    print("EMPTY DataFrame returned!")

# Test 2: end_date_str = "2026-05-27" (tomorrow) with days=1
print("\n" + "=" * 60)
print("TEST 2: get_historical_data('AFCONS', days=1, end_date_str='2026-05-27')")
print("=" * 60)
df2 = get_historical_data("AFCONS", days=1, end_date_str="2026-05-27")
print(f"Total rows: {len(df2)}")
if not df2.empty:
    print(f"Date range: {df2['timestamp'].min()} to {df2['timestamp'].max()}")
    df2["date_str"] = df2["timestamp"].dt.strftime("%Y-%m-%d")
    print(f"Unique dates in data: {sorted(df2['date_str'].unique())}")
    print("\nFirst 3 rows:")
    print(df2.head(3).to_string())
    print("\nLast 3 rows:")
    print(df2.tail(3).to_string())
else:
    print("EMPTY DataFrame returned!")

# Test 3: No end_date (defaults to now) with days=1
print("\n" + "=" * 60)
print("TEST 3: get_historical_data('AFCONS', days=1)")
print("=" * 60)
df3 = get_historical_data("AFCONS", days=1)
print(f"Total rows: {len(df3)}")
if not df3.empty:
    print(f"Date range: {df3['timestamp'].min()} to {df3['timestamp'].max()}")
    df3["date_str"] = df3["timestamp"].dt.strftime("%Y-%m-%d")
    print(f"Unique dates in data: {sorted(df3['date_str'].unique())}")
else:
    print("EMPTY DataFrame returned!")

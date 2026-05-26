"""
test_backtest_local.py -- Local validation of the backtest simulation logic

Tests: date filter, entry-candle skip, SL guard, auto-square-off.
Run with: python backend/test_backtest_local.py
"""

import sys
sys.path.insert(0, "backend")

import pandas as pd
from datetime import datetime, time, date, timedelta

print("=" * 60)
print("  Backtest Simulation Local Test Suite")
print("=" * 60)

PASS = 0
FAIL = 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  [PASS]  {label}")
        PASS += 1
    else:
        print(f"  [FAIL]  {label}")
        if detail:
            print(f"          {detail}")
        FAIL += 1

def _fmt_time(t):
    dt = datetime(2000, 1, 1, t.hour, t.minute, t.second)
    try:
        s = dt.strftime("%-I:%M %p").lower()
    except ValueError:
        s = dt.strftime("%I:%M %p").lower().lstrip("0")
    return s

def _simulate(df, trigger_time_str, entry_price, target, stop_loss, trigger_date):
    """Mirrors the core logic of _backtest_single_alert without DB or API calls."""
    from backtest import parse_trigger_time

    trigger_time = parse_trigger_time(trigger_time_str)
    assert trigger_time is not None, f"Bad time: {trigger_time_str}"

    # SL guard
    if stop_loss >= entry_price:
        stop_loss = round(entry_price * 0.995, 2)

    # Date filter (the critical fix)
    df["date_str"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    df = df[df["date_str"] == trigger_date].reset_index(drop=True)

    if df.empty:
        return "NO_DATA", None, None

    # Time filter
    square_off_time = time(15, 20)
    df["time_only"] = df["timestamp"].dt.time
    post_trigger = df[
        (df["time_only"] >= trigger_time) &
        (df["time_only"] <= square_off_time)
    ].reset_index(drop=True)

    if post_trigger.empty:
        return "NO_CANDLES", None, None

    entry_candle = post_trigger.iloc[0]

    if len(post_trigger) < 2:
        exit_price = float(entry_candle["close"])
        return ("PROFIT" if exit_price > entry_price else
                "LOSS" if exit_price < entry_price else "FLAT"), "3:25 pm", exit_price

    # Skip entry candle; check from second candle onwards
    trade_candles = post_trigger.iloc[1:]

    outcome = exit_time_str = exit_price = None
    for _, candle in trade_candles.iterrows():
        hit_target = candle["high"] >= target
        hit_sl     = candle["low"]  <= stop_loss

        if hit_target and hit_sl:
            outcome = "LOSS"; exit_price = stop_loss
            exit_time_str = _fmt_time(candle["time_only"]); break
        elif hit_target:
            outcome = "PROFIT"; exit_price = target
            exit_time_str = _fmt_time(candle["time_only"]); break
        elif hit_sl:
            outcome = "LOSS"; exit_price = stop_loss
            exit_time_str = _fmt_time(candle["time_only"]); break

    if outcome is None:
        exit_price = float(post_trigger.iloc[-1]["close"])
        outcome = ("PROFIT" if exit_price > entry_price else
                   "LOSS" if exit_price < entry_price else "FLAT")
        exit_time_str = "3:25 pm"

    return outcome, exit_time_str, float(exit_price)


def make_candles(date_str, candles):
    """Build a mock OHLCV DataFrame for the given date."""
    rows = []
    for t_str, o, h, l, c in candles:
        dt = datetime.strptime(f"{date_str} {t_str}", "%Y-%m-%d %H:%M:%S")
        rows.append({"timestamp": dt, "open": o, "high": h, "low": l, "close": c, "volume": 1000})
    return pd.DataFrame(rows)


# TEST 1: Date filter must exclude previous day's candles
print("\n[ TEST 1 ] Date filter - must exclude previous day's candles")
friday = make_candles("2026-05-23", [
    ("15:00:00", 320, 325, 318, 322),
    ("15:05:00", 322, 324, 303, 320),  # low=303 would trip SL!
    ("15:10:00", 320, 323, 319, 321),
    ("15:15:00", 321, 324, 320, 323),
    ("15:20:00", 323, 325, 321, 324),
])
monday = make_candles("2026-05-26", [
    ("09:15:00", 319, 323, 317, 321),
    ("15:00:00", 322, 325, 319, 323),  # entry candle
    ("15:05:00", 323, 326, 319, 324),  # safe (low=319 >> SL=303)
    ("15:10:00", 324, 327, 322, 325),
    ("15:15:00", 325, 328, 323, 326),
    ("15:20:00", 326, 328, 324, 327),
])
df_mixed = pd.concat([friday, monday], ignore_index=True)

outcome, exit_time, exit_price = _simulate(
    df_mixed.copy(), "3:00 pm", 322.0, 360.0, 303.0, "2026-05-26"
)
check("Date filter excludes Friday candles", outcome != "LOSS",
      f"Got outcome={outcome} exit_time={exit_time}")
check("Auto-square-off at 3:25 pm", exit_time == "3:25 pm",
      f"Got exit_time={exit_time}")
check("Exit price is Monday 15:20 close (327)", abs(exit_price - 327.0) < 0.01,
      f"Got exit_price={exit_price}")


# TEST 2: Entry-candle skip
print("\n[ TEST 2 ] Entry-candle skip - SL on entry candle must NOT trigger exit")
df = make_candles("2026-05-26", [
    ("15:00:00", 322, 325, 303, 322),  # entry candle, low hits SL - must be SKIPPED
    ("15:05:00", 322, 326, 319, 324),  # safe candle
    ("15:10:00", 324, 327, 321, 325),
    ("15:20:00", 326, 328, 324, 327),
])
outcome, exit_time, exit_price = _simulate(
    df.copy(), "3:00 pm", 322.0, 360.0, 303.0, "2026-05-26"
)
check("Entry candle SL not triggered", exit_time != "3:00 pm",
      f"Got exit_time={exit_time}")
check("Trade continues after entry candle", outcome != "LOSS" or exit_time != "3:00 pm",
      f"Got outcome={outcome}")


# TEST 3: SL guard
print("\n[ TEST 3 ] SL guard - SL >= entry must be clamped to entry * 0.995")
df = make_candles("2026-05-26", [
    ("15:10:00", 279.85, 283.5, 279.0, 280.5),
    ("15:15:00", 280.5, 284.5, 280.0, 282.9),
    ("15:20:00", 282.9, 283.5, 282.0, 282.9),
])
outcome, exit_time, exit_price = _simulate(
    df.copy(), "3:10 pm", 279.85, 284.35, 281.95, "2026-05-26"
)
check("SL guard prevents phantom loss", outcome != "LOSS",
      f"Got outcome={outcome} exit={exit_price}")
check("Clamped SL lets profit/flat through", outcome in ("PROFIT", "FLAT"),
      f"Got outcome={outcome}")


# TEST 4: Genuine target hit
print("\n[ TEST 4 ] Genuine target hit - after entry candle")
df = make_candles("2026-05-26", [
    ("12:25:00", 328.0, 330.0, 326.0, 329.0),
    ("12:30:00", 329.0, 332.0, 328.0, 331.0),  # high=332 > target=330.65
])
outcome, exit_time, exit_price = _simulate(
    df.copy(), "12:25 pm", 328.5, 330.65, 325.0, "2026-05-26"
)
check("Target hit gives PROFIT", outcome == "PROFIT", f"Got {outcome}")
check("Exit time is 12:30 pm", exit_time == "12:30 pm", f"Got {exit_time}")
check("Exit price equals target (330.65)", abs(exit_price - 330.65) < 0.01, f"Got {exit_price}")


# TEST 5: Genuine SL hit
print("\n[ TEST 5 ] Genuine SL hit after entry candle")
df = make_candles("2026-05-26", [
    ("11:40:00", 4035.0, 4050.0, 4030.0, 4040.0),
    ("11:45:00", 4040.0, 4045.0, 4038.0, 4042.0),
    ("11:50:00", 4042.0, 4048.0, 4019.0, 4021.0),  # hits SL=4018.68
    ("12:05:00", 4021.0, 4030.0, 4018.0, 4022.0),
])
outcome, exit_time, exit_price = _simulate(
    df.copy(), "11:40 am", 4035.0, 4095.52, 4018.68, "2026-05-26"
)
check("SL hit gives LOSS", outcome == "LOSS", f"Got {outcome}")
check("Exit time is 12:05 pm (low=4018 <= SL=4018.68)", exit_time == "12:05 pm", f"Got {exit_time}")


# TEST 6: Auto-square-off single candle
print("\n[ TEST 6 ] Auto-square-off at 3:25 for late entries (single candle)")
df = make_candles("2026-05-26", [
    ("15:20:00", 451.5, 455.0, 441.0, 442.05),
])
outcome, exit_time, exit_price = _simulate(
    df.copy(), "3:20 pm", 451.5, 471.9, 441.3, "2026-05-26"
)
check("Single-candle: exit_time = 3:25 pm", exit_time == "3:25 pm", f"Got {exit_time}")
check("Exit price = close (442.05)", abs(exit_price - 442.05) < 0.01, f"Got {exit_price}")
check("Outcome = LOSS (442.05 < 451.5)", outcome == "LOSS", f"Got {outcome}")


# TEST 7: parse_trigger_time
print("\n[ TEST 7 ] parse_trigger_time handles all formats correctly")
from backtest import parse_trigger_time
formats_to_test = [
    ("3:00 pm",  time(15, 0)),
    ("3:00PM",   time(15, 0)),
    ("12:25 pm", time(12, 25)),
    ("11:40 am", time(11, 40)),
    ("9:15 am",  time(9, 15)),
    ("15:00:00", time(15, 0)),
    ("09:15:00", time(9, 15)),
]
for s, expected in formats_to_test:
    result = parse_trigger_time(s)
    check(f"parse_trigger_time('{s}') = {expected}", result == expected,
          f"Got {result}")


# TEST 8: _fmt_time produces clean strings
print("\n[ TEST 8 ] _fmt_time produces clean strings")
cases = [
    (time(9, 15),  "9:15 am"),
    (time(15, 25), "3:25 pm"),
    (time(12, 30), "12:30 pm"),
    (time(11, 40), "11:40 am"),
    (time(15, 0),  "3:00 pm"),
]
for t_obj, expected in cases:
    result = _fmt_time(t_obj)
    check(f"_fmt_time({t_obj}) = '{expected}'", result == expected, f"Got '{result}'")


# TEST 9: When no candle data exists for the alert date, return NO_DATA (not crash)
print("\n[ TEST 9 ] No candle data for alert date returns NO_DATA gracefully")
df_wrong_day = make_candles("2026-05-25", [
    ("15:00:00", 320, 325, 318, 322),
    ("15:05:00", 322, 324, 303, 320),
])
outcome, exit_time, exit_price = _simulate(
    df_wrong_day.copy(), "3:00 pm", 322.0, 360.0, 303.0, "2026-05-26"
)
check("Returns NO_DATA when no candles for alert date", outcome == "NO_DATA",
      f"Got outcome={outcome}")


print("\n" + "=" * 60)
total = PASS + FAIL
print(f"  Results: {PASS}/{total} passed  {'ALL GOOD' if FAIL == 0 else f'{FAIL} FAILED'}")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)

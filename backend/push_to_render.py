"""
push_to_render.py — Pushes today's recovered alerts to the Render production backend
via the webhook API so they appear in the live dashboard.

Run from the backend/ folder with the venv activated.
"""

import requests
import time

RENDER_URL = "https://trading-engine-58hz.onrender.com"
WEBHOOK_ENDPOINT = f"{RENDER_URL}/api/webhook/chartink"

# Exact stock data recovered from this morning's WhatsApp alerts.
# Trigger prices are the 5-min candle close at each alert time.
ALERTS_TO_PUSH = [
    {"stocks": "ZYDUSLIFE",  "trigger_prices": "1071.00",  "triggered_at": "11:05 am", "scan_name": "Shubham top 10", "alert_name": "Alert for Shubham top 10"},
    {"stocks": "BHEL",       "trigger_prices": "419.00",   "triggered_at": "11:15 am", "scan_name": "Shubham top 10", "alert_name": "Alert for Shubham top 10"},
    {"stocks": "HINDZINC",   "trigger_prices": "634.25",   "triggered_at": "11:25 am", "scan_name": "Shubham top 10", "alert_name": "Alert for Shubham top 10"},
    {"stocks": "JSWINFRA",   "trigger_prices": "277.60",   "triggered_at": "11:28 am", "scan_name": "Shubham top 10", "alert_name": "Alert for Shubham top 10"},
    {"stocks": "LT",         "trigger_prices": "4035.00",  "triggered_at": "11:40 am", "scan_name": "Shubham top 10", "alert_name": "Alert for Shubham top 10"},
    {"stocks": "ENRIN",      "trigger_prices": "3589.20",  "triggered_at": "11:55 am", "scan_name": "Shubham top 10", "alert_name": "Alert for Shubham top 10"},
    {"stocks": "SHYAMMETL",  "trigger_prices": "962.55",   "triggered_at": "12:00 pm", "scan_name": "Shubham top 10", "alert_name": "Alert for Shubham top 10"},
]

print(f"Pushing {len(ALERTS_TO_PUSH)} recovered alerts to Render...\n")

for alert in ALERTS_TO_PUSH:
    stock = alert["stocks"]
    try:
        r = requests.post(WEBHOOK_ENDPOINT, json=alert, timeout=15)
        if r.status_code == 200:
            data = r.json()
            print(f"[OK]  {stock} @ {alert['triggered_at']} -> accepted by Render (status: {data.get('status')})")
        else:
            print(f"[ERR] {stock} -> HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[ERR] {stock} -> {e}")
    
    time.sleep(3)  # Small gap between stocks so Render processes each cleanly

print("\nDone! Check your dashboard in ~20 seconds.")
print("All 7 stocks should now appear in the Live Alert Feed.")
print("LT (ENTER) will also appear in the Backtest Hub.")

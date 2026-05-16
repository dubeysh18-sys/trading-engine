Trading Rule Engine — Backend

A FastAPI application that:
1. Receives Chartink webhook alerts
2. Fetches 5-min OHLCV data from Upstox API V3
3. Evaluates 4 Golden Rules (VWAP, NIFTY Wind, Pivot R1, Rejection Wick)
4. Saves verdicts to SQLite
5. Runs EOD backtests at 15:30 IST

## Setup (First Time)

1. Create your virtual environment:
   python -m venv venv
   venv\Scripts\activate   (Windows)
   source venv/bin/activate (Mac/Linux)

2. Install dependencies:
   pip install -r requirements.txt

3. Configure environment:
   Copy .env.template to .env
   Open .env and paste your Upstox token into UPSTOX_ACCESS_TOKEN

4. Run locally:
   uvicorn main:app --reload --port 8000

5. Test the webhook:
   curl -X POST http://localhost:8000/api/webhook/chartink \
     -H "Content-Type: application/json" \
     -d '{"stocks":"TCS,RELIANCE","trigger_prices":"3500,2800","triggered_at":"10:15 am","scan_name":"Breakout"}'

## Deployment on Render

1. Push this backend/ folder to a GitHub repo
2. Create a new "Web Service" on render.com
3. Set Build Command: pip install -r requirements.txt
4. Set Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
5. Add Environment Variable: UPSTOX_ACCESS_TOKEN = your_token
6. Add Environment Variable: CORS_ORIGINS = https://your-app.vercel.app
7. Create a free UptimeRobot monitor → HTTP(s) → https://your-backend.onrender.com/health
   Set interval to 14 minutes to prevent Render from sleeping during market hours

## API Endpoints

| Method | Path                     | Description                    |
|--------|--------------------------|--------------------------------|
| GET    | /health                  | Keep-alive ping                |
| POST   | /api/webhook/chartink    | Receive Chartink alerts        |
| GET    | /api/alerts              | Today's alerts + verdicts      |
| GET    | /api/nifty-status        | NIFTY LTP vs VWAP              |
| POST   | /api/run-backtest        | Manual backtest trigger        |
| GET    | /api/backtest-results    | Backtest results + summary     |

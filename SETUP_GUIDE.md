# Trading Rule Engine — Complete Setup Guide

## What You're Building

A real-time trading dashboard that:
- Receives stock alerts from Chartink automatically
- Checks 4 rules (VWAP, NIFTY wind, Pivot R1, Rejection wick) using your Upstox data
- Shows live verdicts: **ENTER / WAIT / SKIP**
- Backtests today's ENTER signals at end of day
- Accessible from **any device** (phone, tablet, laptop) via a web link

---

## Prerequisites — Install These First

### 1. Install Python (if not already installed)
- Go to: https://www.python.org/downloads/
- Download Python 3.11+
- During install, check ✅ **"Add Python to PATH"**
- Verify: Open Command Prompt → type `python --version`

### 2. Install Node.js (for the frontend)
- Go to: https://nodejs.org/
- Download the **LTS** version (e.g. 20.x)
- Install with default settings
- Verify: Open Command Prompt → type `node --version`

### 3. Install Git
- Go to: https://git-scm.com/downloads
- Install with default settings
- Verify: Open Command Prompt → type `git --version`

---

## Part 1 — Run Locally (Test on Your Laptop)

### Backend Setup

Open Command Prompt or PowerShell:

```powershell
# Navigate to the backend folder
cd C:\Users\Shubham\.gemini\antigravity\scratch\trading-engine\backend

# Create a virtual environment
python -m venv venv

# Activate it
venv\Scripts\activate

# Install all packages
pip install -r requirements.txt

# Set up your environment file
copy .env.template .env
```

Now open the `.env` file in Notepad and paste your Upstox token:
```
UPSTOX_ACCESS_TOKEN=eyJ0eXAiOiJKV1Qi...   ← paste your full token here
```

Start the backend:
```powershell
uvicorn main:app --reload --port 8000
```

You should see: `Uvicorn running on http://0.0.0.0:8000`

Test it by opening: http://localhost:8000/health → should show `{"status":"ok",...}`

### Frontend Setup

Open a **second** Command Prompt window:

```powershell
cd C:\Users\Shubham\.gemini\antigravity\scratch\trading-engine\frontend

# Install packages (first time only)
npm install

# Start the frontend
npm run dev
```

Open your browser: http://localhost:3000

---

## Part 2 — Deploy to the Internet (Access from Anywhere)

### Step A: Create a GitHub Account (if you don't have one)
- Go to https://github.com → Sign up (free)

### Step B: Push Code to GitHub

```powershell
# In the trading-engine folder
cd C:\Users\Shubham\.gemini\antigravity\scratch\trading-engine

git init
git add .
git commit -m "Initial commit: Trading Rule Engine"

# Create a new repo on GitHub.com → copy the URL → then:
git remote add origin https://github.com/YOUR_USERNAME/trading-engine.git
git push -u origin main
```

### Step C: Deploy Backend on Render (Free)

1. Go to: https://render.com → Sign up with GitHub
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub repo
4. Set **Root Directory** to: `backend`
5. Set **Build Command**: `pip install -r requirements.txt`
6. Set **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
7. Choose **Free** plan
8. Under **Environment Variables**, add:
   - `UPSTOX_ACCESS_TOKEN` = your token
   - `CORS_ORIGINS` = https://your-app.vercel.app (fill in after Step D)
9. Click **Deploy** → wait ~3 minutes
10. Your backend URL will be something like: `https://trading-engine-xxxx.onrender.com`

### Step D: Deploy Frontend on Netlify (Free)

1. Go to: https://netlify.com → Sign up with GitHub
2. Click **"Add new site"** → **"Import an existing project"**
3. Connect your GitHub account and select your `trading-engine` repo
4. The deployment settings will be **auto-filled** (thanks to the `netlify.toml` file we added):
   - Base directory: `frontend`
   - Build command: `npm run build`
   - Publish directory: `frontend/out`
5. Click **"Add environment variables"** and add:
   - `NEXT_PUBLIC_API_URL` = `https://trading-engine-xxxx.onrender.com` (your Render URL from Step C)
6. Click **Deploy site** → wait ~2 minutes
7. Your dashboard URL will be provided by Netlify (e.g., `https://your-site-name.netlify.app`). You can change this in the Site Settings.

### Step E: Keep Render Awake (Free UptimeRobot)

Render's free tier sleeps after 15 minutes of no traffic. To prevent this:

1. Go to: https://uptimerobot.com → Sign up (free)
2. Click **"Add New Monitor"**
3. Type: **HTTP(s)**
4. URL: `https://trading-engine-xxxx.onrender.com/health`
5. Check interval: **14 minutes**
6. Click **Save** ✅

Your backend will now stay awake during market hours.

### Step F: Configure Chartink Webhook

1. Log into Chartink → open your scan alert
2. In the **Webhook URL** field, paste:
   `https://trading-engine-xxxx.onrender.com/api/webhook/chartink`
3. Save the alert ✅

Now every time your Chartink scan fires, it will automatically:
- Send the stocks to your backend
- Fetch Upstox data
- Apply the 4 rules
- Show the verdict on your dashboard in ~10 seconds

---

## Understanding the Dashboard

### Header Bar
- **Pulsing GREEN dot** = NIFTY is above its VWAP → market wind is bullish ✅
- **Pulsing RED dot** = NIFTY is below its VWAP → market is weak ⚠️
- **Clock** = live IST time

### Left Panel: Live Alert Feed
| Column | Meaning |
|--------|---------|
| Time | When Chartink fired the alert |
| Stock | NSE stock symbol |
| Verdict | 🟢 ENTER / 🟡 WAIT / 🔴 SKIP |
| Entry ₹ | Your suggested buy price |
| Target ₹ | Pivot R1 (or R2 if already above R1) |
| Stop Loss ₹ | Max(VWAP-0.1%, candle low) |
| Chart | Opens Kite chart in new tab |

### Right Panel: EOD Backtest Hub
- **Run Daily Backtest** button: simulates all today's ENTER signals
- Auto-runs at 15:30 IST every weekday
- Win Rate % = profitable trades / total backtested × 100
- P&L % = percentage gain/loss per trade

---

## The 4 Golden Rules (in priority order)

| # | Rule | Verdict |
|---|------|---------|
| 1 | Price is within 0.3% below Pivot R1 | 🔴 SKIP — hitting resistance |
| 2 | NIFTY LTP is below NIFTY VWAP | 🔴 SKIP — market weakness |
| 3 | Price is more than 0.8% above VWAP | 🟡 WAIT — overextended |
| 4 | Upper candle wick > candle body | 🔴 SKIP — rejection wick |
| — | None of the above | 🟢 ENTER |

---

## Troubleshooting

**"Cannot connect to backend"** on dashboard:
→ Make sure backend is running (`uvicorn main:app --reload --port 8000`)

**"UPSTOX_ACCESS_TOKEN is not set"**:
→ Check your `.env` file has the token on the correct line

**Chartink webhook not triggering**:
→ Make sure you deployed the backend to Render (local laptop can't receive Chartink webhooks)
→ Test with: `curl -X POST https://your-backend.onrender.com/api/webhook/chartink -H "Content-Type: application/json" -d '{"stocks":"TCS","trigger_prices":"3500","triggered_at":"10:15 am","scan_name":"Test"}'`

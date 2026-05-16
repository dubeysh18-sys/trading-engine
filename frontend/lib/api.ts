/**
 * lib/api.ts — Typed fetch helpers for all backend endpoints
 */

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Types ────────────────────────────────────────────────────────────────────

export interface Alert {
  id:             number;
  stock:          string;
  scan_name:      string | null;
  trigger_time:   string;
  trigger_date:   string;
  trigger_price:  number | null;
  entry:          number | null;
  target:         number | null;
  stop_loss:      number | null;
  vwap:           number | null;
  ema9:           number | null;
  pivot_r1:       number | null;
  verdict:        "ENTER" | "WAIT" | "SKIP" | "ERROR" | "PENDING";
  verdict_reason: string | null;
  created_at:     string | null;
}

export interface NiftyStatus {
  ltp:        number | null;
  vwap:       number | null;
  is_bullish: boolean | null;
  error:      string | null;
}

export interface BacktestTrade {
  stock:       string;
  entry_time:  string | null;
  entry_price: number | null;
  exit_time:   string | null;
  exit_price:  number | null;
  outcome:     "PROFIT" | "LOSS" | "FLAT" | null;
  pnl_pct:     number | null;
  target:      number | null;
  stop_loss:   number | null;
}

export interface BacktestSummary {
  total_enter_alerts: number;
  backtested:         number;
  wins:               number;
  losses:             number;
  flats:              number;
  win_rate_pct:       number;
  net_pnl_pct:        number;
}

export interface BacktestResults {
  summary: BacktestSummary;
  trades:  BacktestTrade[];
}

// ── Fetch Helpers ─────────────────────────────────────────────────────────────

export async function fetchAlerts(): Promise<Alert[]> {
  const res = await fetch(`${API}/api/alerts`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Alerts fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchNiftyStatus(): Promise<NiftyStatus> {
  const res = await fetch(`${API}/api/nifty-status`, { cache: "no-store" });
  if (!res.ok) throw new Error(`NIFTY status fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchBacktestResults(): Promise<BacktestResults> {
  const res = await fetch(`${API}/api/backtest-results`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Backtest results fetch failed: ${res.status}`);
  return res.json();
}

export async function triggerBacktest(): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API}/api/run-backtest`, {
    method: "POST",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Backtest trigger failed: ${res.status}`);
  return res.json();
}

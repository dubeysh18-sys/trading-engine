/**
 * lib/api.ts — Typed fetch helpers for all backend endpoints
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export { BASE_URL as API };

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
  outcome:     "PROFIT" | "LOSS" | "FLAT" | "PENDING" | null;
  pnl_pct:     number | null;
  quantity:    number | null;
  pnl_amount:  number | null;
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
  net_pnl_amount:     number;
}

export interface BacktestResults {
  summary: BacktestSummary;
  trades:  BacktestTrade[];
}

// ── Live Price Types ──────────────────────────────────────────────────────────

export interface LivePrice {
  stock:       string;
  alert_id:    number;
  entry_price: number | null;
  target:      number | null;
  stop_loss:   number | null;
  ltp:         number | null;
  pnl_pct:     number | null;
  pnl_amount:  number | null;
  quantity:    number | null;
  status:      "ACTIVE" | "PROFIT" | "LOSS" | "FLAT" | "PENDING";
}

// ── Fetch Helpers ─────────────────────────────────────────────────────────────

export async function fetchAlerts(date?: string): Promise<Alert[]> {
  const url = date ? `${BASE_URL}/api/alerts?date=${date}` : `${BASE_URL}/api/alerts`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Alerts fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchNiftyStatus(): Promise<NiftyStatus> {
  const res = await fetch(`${BASE_URL}/api/nifty-status`, { cache: "no-store" });
  if (!res.ok) throw new Error(`NIFTY status fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchBacktestResults(date?: string): Promise<BacktestResults> {
  const url = date ? `${BASE_URL}/api/backtest-results?date=${date}` : `${BASE_URL}/api/backtest-results`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Backtest results fetch failed: ${res.status}`);
  return res.json();
}

export async function triggerBacktest(): Promise<{ status: string; message: string }> {
  const res = await fetch(`${BASE_URL}/api/run-backtest`, {
    method: "POST",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Backtest trigger failed: ${res.status}`);
  return res.json();
}

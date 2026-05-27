/**
 * lib/api.ts — Simplified API configuration and types
 */

export const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "https://trading-engine-58hz.onrender.com";
export const WS_URL = BASE_URL.replace(/^http/, "ws") + "/ws/prices";

export interface Alert {
  stock: string;
  trigger_price: number;
  trigger_time: string;
  verdict: "ENTER" | "WAIT" | "SKIP" | "ERROR";
  reason: string;
}

export interface ActiveTrade {
  stock: string;
  entry_price: number;
  target: number;
  stop_loss: number;
  entered_at: string;
  status: string;
  ltp: number;
  pnl_pct: number;
}

export interface ExitResult {
  stock: string;
  pct: number;
  hit: "TARGET" | "SL";
  exit_price?: number;
  exit_time?: string;
}

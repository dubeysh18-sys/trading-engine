"use client";

import { useEffect, useState, useCallback } from "react";
import {
  BarChart3,
  Play,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Minus,
} from "lucide-react";
import {
  fetchBacktestResults,
  fetchLivePrices,
  triggerBacktest,
  type BacktestTrade,
  type BacktestSummary,
  type LivePrice,
} from "@/lib/api";

function fmt(val: number | null | undefined, decimals = 2): string {
  if (val === null || val === undefined) return "—";
  return val.toFixed(decimals);
}

function isMarketOpen(): boolean {
  const now = new Date();
  const ist = new Date(
    now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" })
  );
  const h = ist.getHours();
  const m = ist.getMinutes();
  const day = ist.getDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const mins = h * 60 + m;
  return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30;
}

interface StatCardProps {
  label: string;
  value: string | number;
  color?: string;
  sub?: string;
}
function StatCard({ label, value, color, sub }: StatCardProps) {
  return (
    <div className="stat-card" style={{ flex: 1, minWidth: 0 }}>
      <div className="stat-value" style={{ color: color || "#e2e8f0" }}>
        {value}
      </div>
      <div className="stat-label">{label}</div>
      {sub && (
        <div style={{ fontSize: 10, color: "#4b5563", marginTop: 2 }}>{sub}</div>
      )}
    </div>
  );
}

function OutcomeIcon({ outcome }: { outcome: string | null }) {
  if (outcome === "PROFIT")
    return <TrendingUp size={13} color="var(--accent-green)" />;
  if (outcome === "LOSS")
    return <TrendingDown size={13} color="var(--accent-red)" />;
  return <Minus size={13} color="var(--text-secondary)" />;
}

const timeToMinutes = (timeStr: string | null | undefined): number => {
  if (!timeStr || timeStr === "—") return -1;
  const match = timeStr.toLowerCase().match(/(\d+):(\d+)\s*(am|pm)?/);
  if (!match) return -1;
  let [_, hoursStr, minutesStr, ampm] = match;
  let hours = parseInt(hoursStr, 10);
  const minutes = parseInt(minutesStr, 10);
  if (ampm === "pm" && hours < 12) hours += 12;
  if (ampm === "am" && hours === 12) hours = 0;
  return hours * 60 + minutes;
};

export default function BacktestHub({ selectedDate }: { selectedDate: string }) {
  const [summary, setSummary]       = useState<BacktestSummary | null>(null);
  const [trades, setTrades]         = useState<BacktestTrade[]>([]);
  const [livePrices, setLivePrices] = useState<Record<string, LivePrice>>({});
  const [loading, setLoading]       = useState(true);
  const [running, setRunning]       = useState(false);
  const [runMsg, setRunMsg]         = useState<string | null>(null);
  const [error, setError]           = useState<string | null>(null);
  const [lastFetch, setLastFetch]   = useState<Date | null>(null);
  const [marketOpen, setMarketOpen] = useState(false);

  const [sortField, setSortField] = useState<"entry_time" | "exit_time" | "pnl_amount" | null>(null);
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");

  const toggleSort = (field: "entry_time" | "exit_time" | "pnl_amount") => {
    if (sortField === field) {
      setSortDirection(prev => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDirection("desc"); // Default to desc
    }
  };

  useEffect(() => {
    setMarketOpen(isMarketOpen());
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [data, priceData] = await Promise.all([
        fetchBacktestResults(selectedDate),
        fetchLivePrices(selectedDate).catch(() => []),
      ]);
      setSummary(data.summary);
      setTrades(data.trades);
      
      const map: Record<string, LivePrice> = {};
      priceData.forEach((p: LivePrice) => { map[p.stock] = p; });
      setLivePrices(map);
      
      setLastFetch(new Date());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load results");
    } finally {
      setLoading(false);
    }
  }, [selectedDate]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10_000); // poll every 10s for real-time live floating P&L
    return () => clearInterval(id);
  }, [refresh]);

  const handleRunBacktest = async () => {
    setRunning(true);
    setRunMsg(null);
    try {
      const res = await triggerBacktest();
      setRunMsg(res.message);
      setTimeout(refresh, 5000); // refresh after 5s
    } catch (e: unknown) {
      setRunMsg(e instanceof Error ? e.message : "Backtest trigger failed");
    } finally {
      setRunning(false);
    }
  };

  const pnlColor = (pnl: number | null) => {
    if (pnl === null) return "#94a3b8";
    return pnl > 0 ? "var(--accent-green)" : pnl < 0 ? "var(--accent-red)" : "#94a3b8";
  };

  const outcomeClass = (outcome: string | null) => {
    if (outcome === "PROFIT") return "outcome-profit";
    if (outcome === "LOSS") return "outcome-loss";
    if (outcome === "PENDING") return "outcome-pending";
    return "outcome-flat";
  };

  const winRateColor =
    !summary || summary.backtested === 0
      ? "#94a3b8"
      : summary.win_rate_pct >= 60
      ? "var(--accent-green)"
      : summary.win_rate_pct >= 40
      ? "var(--accent-yellow)"
      : "var(--accent-red)";

  // Compute live adjusted summary
  const adjustedSummary = summary ? { ...summary } : null;
  const augmentedTrades = trades.map(t => {
    if (t.outcome === "PENDING" && livePrices[t.stock]) {
      const ltp = livePrices[t.stock].ltp;
      if (ltp !== null) {
        const pct = t.entry_price ? (ltp - t.entry_price) / t.entry_price * 100 : 0;
        const amt = (t.quantity || 0) * (ltp - (t.entry_price || 0));
        return { ...t, live_pnl_pct: pct, live_pnl_amt: amt, ltp };
      }
    }
    return t;
  });

  if (adjustedSummary) {
    let totalPct = summary!.net_pnl_pct * summary!.backtested;
    let totalAmt = summary!.net_pnl_amount;
    let countedTrades = summary!.backtested;

    for (const t of augmentedTrades) {
      if (t.outcome === "PENDING" && "live_pnl_pct" in t) {
        totalPct += (t as any).live_pnl_pct;
        totalAmt += (t as any).live_pnl_amt;
        countedTrades++;
      }
    }
    
    if (countedTrades > 0) {
      adjustedSummary.net_pnl_pct = totalPct / countedTrades;
      adjustedSummary.net_pnl_amount = totalAmt;
    }
  }

  // Sort augmented trades
  const sortedTrades = [...augmentedTrades].sort((a: any, b: any) => {
    if (!sortField) return 0;

    let valA = 0;
    let valB = 0;

    if (sortField === "pnl_amount") {
      valA = a.pnl_amount ?? a.live_pnl_amt ?? 0;
      valB = b.pnl_amount ?? b.live_pnl_amt ?? 0;
    } else if (sortField === "entry_time") {
      valA = timeToMinutes(a.entry_time);
      valB = timeToMinutes(b.entry_time);
    } else if (sortField === "exit_time") {
      valA = timeToMinutes(a.exit_time);
      valB = timeToMinutes(b.exit_time);
    }

    if (valA === valB) return 0;
    if (sortDirection === "asc") {
      return valA > valB ? 1 : -1;
    } else {
      return valA < valB ? 1 : -1;
    }
  });

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header */}
      <div className="card-header">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <BarChart3 size={16} color="var(--accent-blue)" />
          <span style={{ fontWeight: 600, fontSize: 14, color: "#e2e8f0" }}>
            EOD Backtester Hub
          </span>
          {summary && summary.backtested > 0 && (
            <span
              style={{
                background: "rgba(59,130,246,0.12)",
                color: "var(--accent-blue)",
                border: "1px solid rgba(59,130,246,0.25)",
                borderRadius: 9999,
                fontSize: 10,
                fontWeight: 700,
                padding: "1px 7px",
              }}
            >
              {summary.backtested} TRADES
            </span>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            onClick={refresh}
            style={{
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "#4b5563",
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontSize: 11,
              padding: "4px 8px",
              borderRadius: 4,
              transition: "color 0.15s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#94a3b8")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "#4b5563")}
          >
            <RefreshCw size={12} />
            {lastFetch
              ? lastFetch.toLocaleTimeString("en-IN", {
                  hour: "2-digit",
                  minute: "2-digit",
                  hour12: true,
                })
              : ""}
          </button>

          <button
            id="run-backtest-btn"
            className="btn-primary"
            onClick={handleRunBacktest}
            disabled={running}
            title={
              marketOpen
                ? "Backtest runs automatically at 15:30. You can still trigger manually."
                : "Click to run the EOD backtest"
            }
          >
            {running ? (
              <RefreshCw size={13} style={{ animation: "spin 1s linear infinite" }} />
            ) : (
              <Play size={13} />
            )}
            {running ? "Running..." : "Run Daily Backtest"}
          </button>
        </div>
      </div>

      {/* Run message */}
      {runMsg && (
        <div
          style={{
            margin: "12px 16px 0",
            padding: "10px 14px",
            background: "rgba(59,130,246,0.08)",
            border: "1px solid rgba(59,130,246,0.2)",
            borderRadius: 8,
            color: "#60a5fa",
            fontSize: 12,
          }}
        >
          ✓ {runMsg}
        </div>
      )}

      {/* Summary Cards */}
      <div
        style={{
          display: "flex",
          gap: 10,
          padding: "14px 16px",
          borderBottom: "1px solid #1e2d45",
        }}
      >
        <StatCard
          label="ENTER Alerts Today"
          value={loading ? "…" : summary?.total_enter_alerts ?? 0}
          color="#e2e8f0"
        />
        <StatCard
          label="Win Rate"
          value={
            loading
              ? "…"
              : adjustedSummary && adjustedSummary.backtested > 0
              ? `${adjustedSummary.win_rate_pct}%`
              : "—"
          }
          color={winRateColor}
          sub={
            adjustedSummary && adjustedSummary.backtested > 0
              ? `${adjustedSummary.wins}W / ${adjustedSummary.losses}L / ${adjustedSummary.flats}F`
              : undefined
          }
        />
        <StatCard
          label="Net Avg P&L"
          value={
            loading
              ? "…"
              : adjustedSummary && (adjustedSummary.backtested > 0 || augmentedTrades.some(t => t.outcome === "PENDING"))
              ? `${adjustedSummary.net_pnl_pct > 0 ? "+" : ""}${adjustedSummary.net_pnl_pct.toFixed(2)}%`
              : "—"
          }
          color={
            adjustedSummary && adjustedSummary.net_pnl_pct > 0
              ? "var(--accent-green)"
              : adjustedSummary && adjustedSummary.net_pnl_pct < 0
              ? "var(--accent-red)"
              : "#94a3b8"
          }
          sub="Average % per trade (inc. live)"
        />
        <StatCard
          label="Net P&L (₹)"
          value={
            loading
              ? "…"
              : adjustedSummary && (adjustedSummary.backtested > 0 || augmentedTrades.some(t => t.outcome === "PENDING"))
              ? `${adjustedSummary.net_pnl_amount > 0 ? "+" : ""}₹${adjustedSummary.net_pnl_amount.toLocaleString("en-IN", {maximumFractionDigits:2})}`
              : "—"
          }
          color={
            adjustedSummary && adjustedSummary.net_pnl_amount > 0
              ? "var(--accent-green)"
              : adjustedSummary && adjustedSummary.net_pnl_amount < 0
              ? "var(--accent-red)"
              : "#94a3b8"
          }
          sub="50k × 4x leverage"
        />
      </div>

      {/* Market-hours notice */}
      {marketOpen && (
        <div
          style={{
            margin: "10px 16px 0",
            padding: "8px 12px",
            background: "rgba(251,191,36,0.06)",
            border: "1px solid rgba(251,191,36,0.2)",
            borderRadius: 6,
            color: "var(--accent-yellow)",
            fontSize: 11,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          ⏳ Market is open. Backtest auto-runs at 15:30 IST. Manual trigger available anytime.
        </div>
      )}

      {/* Trades Table */}
      <div className="card-body" style={{ flex: 1 }}>
        {loading && trades.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "#4b5563" }}>
            <RefreshCw size={20} style={{ margin: "0 auto 10px", display: "block", opacity: 0.4 }} />
            Loading results...
          </div>
        ) : error ? (
          <div
            style={{
              padding: 20,
              margin: 16,
              background: "rgba(255,77,77,0.08)",
              border: "1px solid rgba(255,77,77,0.2)",
              borderRadius: 8,
              color: "var(--accent-red)",
              fontSize: 12,
            }}
          >
            ⚠ {error}
          </div>
        ) : trades.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "#4b5563" }}>
            <div style={{ fontSize: 28, marginBottom: 10 }}>📊</div>
            <div style={{ fontSize: 13 }}>No backtest results yet.</div>
            <div style={{ fontSize: 11, marginTop: 6 }}>
              Results appear after clicking "Run Daily Backtest" or at 15:30 IST.
            </div>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Stock</th>
                <th onClick={() => toggleSort("entry_time")} style={{ cursor: "pointer", userSelect: "none" }}>
                  Entry Time {sortField === "entry_time" && (sortDirection === "asc" ? " ▲" : " ▼")}
                </th>
                <th style={{ textAlign: "right" }}>Entry ₹</th>
                <th onClick={() => toggleSort("exit_time")} style={{ cursor: "pointer", userSelect: "none" }}>
                  Exit Time {sortField === "exit_time" && (sortDirection === "asc" ? " ▲" : " ▼")}
                </th>
                <th style={{ textAlign: "right" }}>Exit ₹</th>
                <th onClick={() => toggleSort("pnl_amount")} style={{ cursor: "pointer", userSelect: "none", textAlign: "right" }}>
                  P&L {sortField === "pnl_amount" && (sortDirection === "asc" ? " ▲" : " ▼")}
                </th>
                <th style={{ textAlign: "right" }}>Target ₹</th>
                <th style={{ textAlign: "right" }}>SL ₹</th>
              </tr>
            </thead>
            <tbody>
              {sortedTrades.map((trade: any, idx) => (
                <tr key={idx} className="animate-fade-in">
                  <td>
                    <span
                      style={{
                        fontWeight: 600,
                        fontFamily: "JetBrains Mono, monospace",
                        fontSize: 12,
                        color: "#e2e8f0",
                      }}
                    >
                      {trade.stock}
                    </span>
                  </td>

                  <td style={{ color: "#94a3b8", fontFamily: "JetBrains Mono, monospace" }}>
                    {trade.entry_time ?? "—"}
                  </td>

                  <td
                    style={{
                      textAlign: "right",
                      fontFamily: "JetBrains Mono, monospace",
                      color: "#e2e8f0",
                    }}
                  >
                    {fmt(trade.entry_price)}
                  </td>

                  <td style={{ color: "#94a3b8", fontFamily: "JetBrains Mono, monospace" }}>
                    {trade.exit_time ?? "—"}
                  </td>

                  <td
                    style={{
                      textAlign: "right",
                      fontFamily: "JetBrains Mono, monospace",
                      color: "#e2e8f0",
                    }}
                  >
                    {trade.outcome === "PENDING" && trade.ltp 
                      ? <span style={{ color: "var(--accent-yellow)" }}>{fmt(trade.ltp)}</span>
                      : fmt(trade.exit_price)}
                  </td>

                  <td style={{ textAlign: "right", fontFamily: "JetBrains Mono, monospace" }}>
                    <span
                      style={{
                        color: pnlColor(trade.pnl_amount ?? trade.live_pnl_amt),
                        fontWeight: 600,
                      }}
                    >
                      {(() => {
                        const amt = trade.pnl_amount ?? trade.live_pnl_amt;
                        const pct = trade.pnl_pct ?? trade.live_pnl_pct;
                        if (amt === undefined || amt === null) return "Live";
                        const sign = amt > 0 ? "+" : amt < 0 ? "-" : "";
                        const pctStr = pct !== undefined && pct !== null ? `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%` : "0.00%";
                        return `${sign}₹${Math.abs(amt).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 2 })} (${pctStr})`;
                      })()}
                    </span>
                  </td>

                  <td style={{ textAlign: "right", color: "#60a5fa", fontFamily: "JetBrains Mono, monospace" }}>
                    {fmt(trade.target)}
                  </td>

                  <td style={{ textAlign: "right", color: "var(--accent-red)", fontFamily: "JetBrains Mono, monospace" }}>
                    {fmt(trade.stop_loss)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Footer */}
      <div
        style={{
          padding: "8px 16px",
          borderTop: "1px solid #1e2d45",
          color: "#4b5563",
          fontSize: 11,
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>Auto-backtest: 15:30 IST (Mon–Fri)</span>
        <span>Auto-refreshes every 60s</span>
      </div>

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

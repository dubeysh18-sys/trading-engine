"use client";

import { useWebSocket } from "./WebSocketListener";
import { TrendingUp, TrendingDown, Minus, Activity, ShieldCheck } from "lucide-react";

function fmt(val: number | null | undefined, decimals = 2): string {
  if (val === null || val === undefined) return "—";
  return val.toFixed(decimals);
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
      <div className="stat-value" style={{ color: color || "#e2e8f0", fontSize: 20, fontWeight: 700 }}>
        {value}
      </div>
      <div className="stat-label" style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase", fontWeight: 700, marginTop: 4 }}>
        {label}
      </div>
      {sub && (
        <div style={{ fontSize: 10, color: "#4b5563", marginTop: 2 }}>{sub}</div>
      )}
    </div>
  );
}

export default function ActiveTrades() {
  const { activeTrades, exitResults } = useWebSocket();

  const activeCount = Object.keys(activeTrades).length;
  const closedCount = exitResults.length;

  // Calculate Win Rate
  const wins = exitResults.filter((t) => t.hit === "TARGET").length;
  const winRate = closedCount > 0 ? (wins / closedCount) * 100 : 0.0;

  // Calculate Net P&L %
  const netPnl = exitResults.reduce((sum, t) => sum + t.pct, 0.0);

  // Convert activeTrades map to array
  const tradesList = Object.values(activeTrades);

  return (
    <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      {/* Header */}
      <div
        className="card-header"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: "1px solid #1e2d45",
          padding: "12px 16px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: "0.02em", color: "#f8fafc", margin: 0 }}>
            TRADE HUB (IN-MEMORY)
          </h2>
        </div>
      </div>

      {/* Stats Cards Row */}
      <div
        style={{
          display: "flex",
          gap: 12,
          padding: 16,
          borderBottom: "1px solid #1e2d45",
          background: "#070b12",
        }}
      >
        <StatCard label="Active Trades" value={activeCount} color="#60a5fa" />
        <StatCard label="Closed Trades" value={closedCount} color="#94a3b8" />
        <StatCard
          label="Win Rate"
          value={closedCount > 0 ? `${fmt(winRate, 1)}%` : "0.0%"}
          color={winRate > 50 ? "var(--accent-green)" : winRate > 0 ? "var(--accent-yellow)" : "#94a3b8"}
          sub={`${wins} wins / ${closedCount - wins} losses`}
        />
        <StatCard
          label="Net P&L"
          value={`${netPnl >= 0 ? "+" : ""}${fmt(netPnl, 2)}%`}
          color={netPnl > 0 ? "var(--accent-green)" : netPnl < 0 ? "var(--accent-red)" : "#94a3b8"}
        />
      </div>

      {/* Workspace Panel */}
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateRows: "1fr 1fr",
          minHeight: 0,
        }}
      >
        {/* Upper Grid: Active Trades */}
        <div
          style={{
            borderBottom: "1px solid #1e2d45",
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
          }}
        >
          <div
            style={{
              padding: "10px 16px",
              background: "#090d16",
              borderBottom: "1px solid #121c2c",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <Activity size={14} color="#60a5fa" />
            <span style={{ fontSize: 11, fontWeight: 700, color: "#f8fafc", letterSpacing: "0.03em" }}>
              ACTIVE MONITORING ({activeCount})
            </span>
          </div>

          <div style={{ flex: 1, overflowY: "auto", padding: 8 }}>
            {tradesList.length === 0 ? (
              <div style={{ padding: 24, textAlign: "center", color: "#4b5563", fontSize: 12 }}>
                No active trades currently open.
              </div>
            ) : (
              <table className="data-table" style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>Stock</th>
                    <th style={{ textAlign: "right", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>Entry ₹</th>
                    <th style={{ textAlign: "right", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>Target ₹</th>
                    <th style={{ textAlign: "right", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>SL ₹</th>
                    <th style={{ textAlign: "right", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>LTP ₹</th>
                    <th style={{ textAlign: "right", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>Live P&L</th>
                    <th style={{ textAlign: "center", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {tradesList.map((trade) => {
                    const isProfit = trade.pnl_pct >= 0;
                    return (
                      <tr key={trade.stock} style={{ borderBottom: "1px solid #0f1923" }}>
                        <td style={{ padding: "6px 10px", fontWeight: 700, color: "#cbd5e1" }}>
                          {trade.stock}
                        </td>
                        <td style={{ padding: "6px 10px", textAlign: "right", fontFamily: "monospace" }}>
                          {fmt(trade.entry_price)}
                        </td>
                        <td style={{ padding: "6px 10px", textAlign: "right", fontFamily: "monospace", color: "#60a5fa" }}>
                          {fmt(trade.target)}
                        </td>
                        <td style={{ padding: "6px 10px", textAlign: "right", fontFamily: "monospace", color: "var(--accent-red)" }}>
                          {fmt(trade.stop_loss)}
                        </td>
                        <td style={{ padding: "6px 10px", textAlign: "right", fontFamily: "monospace" }}>
                          {fmt(trade.ltp)}
                        </td>
                        <td
                          style={{
                            padding: "6px 10px",
                            textAlign: "right",
                            fontFamily: "monospace",
                            fontWeight: 700,
                            color: isProfit ? "var(--accent-green)" : "var(--accent-red)",
                          }}
                        >
                          {isProfit ? "+" : ""}
                          {fmt(trade.pnl_pct)}%
                        </td>
                        <td style={{ padding: "6px 10px", textAlign: "center", fontSize: 10, color: "#64748b" }}>
                          {trade.entered_at}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Lower Grid: Exits / Closed Trades */}
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div
            style={{
              padding: "10px 16px",
              background: "#090d16",
              borderBottom: "1px solid #121c2c",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <ShieldCheck size={14} color="var(--accent-green)" />
            <span style={{ fontSize: 11, fontWeight: 700, color: "#f8fafc", letterSpacing: "0.03em" }}>
              EXIT HISTOGRAM ({closedCount})
            </span>
          </div>

          <div style={{ flex: 1, overflowY: "auto", padding: 8 }}>
            {exitResults.length === 0 ? (
              <div style={{ padding: 24, textAlign: "center", color: "#4b5563", fontSize: 12 }}>
                No completed trades yet today.
              </div>
            ) : (
              <table className="data-table" style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>Stock</th>
                    <th style={{ textAlign: "center", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>Outcome</th>
                    <th style={{ textAlign: "right", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>Exit Price ₹</th>
                    <th style={{ textAlign: "right", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>P&L %</th>
                    <th style={{ textAlign: "center", padding: "6px 10px", fontSize: 10, color: "#64748b" }}>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {[...exitResults].reverse().map((result, idx) => {
                    const isTarget = result.hit === "TARGET";
                    return (
                      <tr key={idx} style={{ borderBottom: "1px solid #0f1923" }}>
                        <td style={{ padding: "6px 10px", fontWeight: 700, color: "#94a3b8" }}>
                          {result.stock}
                        </td>
                        <td style={{ padding: "6px 10px", textAlign: "center" }}>
                          <span
                            className={isTarget ? "outcome-profit" : "outcome-loss"}
                            style={{
                              fontSize: 10,
                              fontWeight: 700,
                              padding: "2px 6px",
                              borderRadius: 4,
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 4,
                            }}
                          >
                            {isTarget ? (
                              <>
                                <TrendingUp size={11} /> TARGET
                              </>
                            ) : (
                              <>
                                <TrendingDown size={11} /> STOP LOSS
                              </>
                            )}
                          </span>
                        </td>
                        <td style={{ padding: "6px 10px", textAlign: "right", fontFamily: "monospace" }}>
                          {fmt(result.exit_price)}
                        </td>
                        <td
                          style={{
                            padding: "6px 10px",
                            textAlign: "right",
                            fontFamily: "monospace",
                            fontWeight: 700,
                            color: result.pct >= 0 ? "var(--accent-green)" : "var(--accent-red)",
                          }}
                        >
                          {result.pct >= 0 ? "+" : ""}
                          {fmt(result.pct)}%
                        </td>
                        <td style={{ padding: "6px 10px", textAlign: "center", fontSize: 10, color: "#64748b" }}>
                          {result.exit_time || "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState, useCallback } from "react";
import { ExternalLink, RefreshCw, Zap } from "lucide-react";
import { fetchAlerts, type Alert } from "@/lib/api";
import VerdictPill from "./VerdictPill";

function fmt(val: number | null | undefined, decimals = 2): string {
  if (val === null || val === undefined) return "—";
  return val.toFixed(decimals);
}

function kiteLink(stock: string): string {
  return `https://kite.zerodha.com/chart/web/ciq/NSE/${stock}/EQ`;
}

function formatTime(timeStr: string | null): string {
  if (!timeStr) return "—";
  // Normalize "2:34 pm" → "14:34"
  try {
    const lower = timeStr.toLowerCase().trim();
    const d = new Date(`1970-01-01 ${lower}`);
    if (!isNaN(d.getTime())) {
      return d.toLocaleTimeString("en-IN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    }
  } catch {}
  return timeStr;
}

export default function AlertFeed({ selectedDate }: { selectedDate: string }) {
  const [alerts, setAlerts]     = useState<Alert[]>([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const [lastFetch, setLastFetch] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchAlerts(selectedDate);
      setAlerts(data);
      setLastFetch(new Date());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load alerts");
    } finally {
      setLoading(false);
    }
  }, [selectedDate]);

  useEffect(() => {
    refresh();
    
    // Check if selectedDate is today
    const today = new Date().toLocaleDateString("en-CA");
    if (selectedDate !== today) return; // No SSE for past dates

    // Connect to SSE for real-time updates
    const API = process.env.NEXT_PUBLIC_API_URL || "https://trading-engine-58hz.onrender.com";
    const eventSource = new EventSource(`${API}/api/alerts/stream`);
    
    eventSource.addEventListener("new_alert", (event) => {
      try {
        const newAlert = JSON.parse(event.data);
        setAlerts((prev) => [newAlert, ...prev]);
        setLastFetch(new Date());
      } catch (e) {
        console.error("Failed to parse SSE data", e);
      }
    });

    eventSource.onerror = () => {
      console.error("SSE connection error");
      eventSource.close();
    };

    return () => {
      eventSource.close();
    };
  }, [refresh, selectedDate]);

  const enterCount = alerts.filter((a) => a.verdict === "ENTER").length;
  const skipCount  = alerts.filter((a) => a.verdict === "SKIP").length;
  const waitCount  = alerts.filter((a) => a.verdict === "WAIT").length;

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header */}
      <div className="card-header">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Zap size={16} color="var(--accent-yellow)" />
          <span style={{ fontWeight: 600, fontSize: 14, color: "#e2e8f0" }}>
            Live Alert Feed
          </span>
          <span
            style={{
              background: "rgba(251,191,36,0.12)",
              color: "var(--accent-yellow)",
              border: "1px solid rgba(251,191,36,0.25)",
              borderRadius: 9999,
              fontSize: 10,
              fontWeight: 700,
              padding: "1px 7px",
              letterSpacing: "0.04em",
            }}
          >
            LIVE
          </span>
        </div>

        {/* Mini stats */}
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ display: "flex", gap: 8, fontSize: 11 }}>
            <span style={{ color: "var(--accent-green)" }}>
              ↑ {enterCount} ENTER
            </span>
            <span style={{ color: "var(--accent-yellow)" }}>
              ⏸ {waitCount} WAIT
            </span>
            <span style={{ color: "var(--accent-red)" }}>
              ✕ {skipCount} SKIP
            </span>
          </div>
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
              padding: "2px 6px",
              borderRadius: 4,
              transition: "color 0.15s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#94a3b8")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "#4b5563")}
            title="Refresh now"
          >
            <RefreshCw size={12} />
            {lastFetch
              ? lastFetch.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true })
              : ""}
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="card-body" style={{ flex: 1 }}>
        {loading && alerts.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "#4b5563" }}>
            <RefreshCw size={20} style={{ margin: "0 auto 10px", display: "block", opacity: 0.4 }} />
            Loading alerts...
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
            <div style={{ color: "#4b5563", marginTop: 4, fontSize: 11 }}>
              Make sure the backend is running at {process.env.NEXT_PUBLIC_API_URL}
            </div>
          </div>
        ) : alerts.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "#4b5563" }}>
            <div style={{ fontSize: 28, marginBottom: 10 }}>📡</div>
            <div style={{ fontSize: 13 }}>Waiting for Chartink alerts...</div>
            <div style={{ fontSize: 11, marginTop: 6 }}>
              Alerts will appear here within seconds of your Chartink scan firing.
            </div>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Stock</th>
                <th>Verdict</th>
                <th style={{ textAlign: "right" }}>Entry ₹</th>
                <th style={{ textAlign: "right" }}>Target ₹</th>
                <th style={{ textAlign: "right" }}>Stop Loss ₹</th>
                <th style={{ textAlign: "right" }}>VWAP</th>
                <th style={{ textAlign: "right" }}>R1</th>
                <th>Chart</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((alert) => (
                <tr key={alert.id} className="animate-fade-in">
                  <td style={{ color: "#94a3b8" }}>{formatTime(alert.trigger_time)}</td>

                  <td>
                    <span
                      style={{
                        fontWeight: 600,
                        color: "#e2e8f0",
                        fontFamily: "JetBrains Mono, monospace",
                        fontSize: 12,
                      }}
                    >
                      {alert.stock}
                    </span>
                    {alert.scan_name && (
                      <div style={{ fontSize: 10, color: "#4b5563", marginTop: 1 }}>
                        {alert.scan_name}
                      </div>
                    )}
                  </td>

                  <td>
                    <VerdictPill verdict={alert.verdict} reason={alert.verdict_reason} />
                    {alert.verdict_reason && (
                      <div style={{ fontSize: 10, color: "#4b5563", marginTop: 2, maxWidth: 160, whiteSpace: "normal" }}>
                        {alert.verdict_reason}
                      </div>
                    )}
                  </td>

                  <td style={{ textAlign: "right", fontFamily: "JetBrains Mono, monospace" }}>
                    {alert.entry ? (
                      <span style={{ color: "var(--accent-green)", fontWeight: 600 }}>
                        {fmt(alert.entry)}
                      </span>
                    ) : (
                      <span style={{ color: "#4b5563" }}>—</span>
                    )}
                  </td>

                  <td style={{ textAlign: "right", fontFamily: "JetBrains Mono, monospace" }}>
                    {alert.target ? (
                      <span style={{ color: "#60a5fa" }}>{fmt(alert.target)}</span>
                    ) : (
                      <span style={{ color: "#4b5563" }}>—</span>
                    )}
                  </td>

                  <td style={{ textAlign: "right", fontFamily: "JetBrains Mono, monospace" }}>
                    {alert.stop_loss ? (
                      <span style={{ color: "var(--accent-red)" }}>{fmt(alert.stop_loss)}</span>
                    ) : (
                      <span style={{ color: "#4b5563" }}>—</span>
                    )}
                  </td>

                  <td style={{ textAlign: "right", color: "#94a3b8", fontFamily: "JetBrains Mono, monospace" }}>
                    {fmt(alert.vwap)}
                  </td>

                  <td style={{ textAlign: "right", color: "#94a3b8", fontFamily: "JetBrains Mono, monospace" }}>
                    {fmt(alert.pivot_r1)}
                  </td>

                  <td>
                    <a
                      href={kiteLink(alert.stock)}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{
                        color: "#3b82f6",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 3,
                        fontSize: 11,
                        textDecoration: "none",
                        padding: "2px 6px",
                        borderRadius: 4,
                        border: "1px solid rgba(59,130,246,0.25)",
                        transition: "all 0.15s",
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = "rgba(59,130,246,0.1)";
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = "transparent";
                      }}
                    >
                      Kite <ExternalLink size={10} />
                    </a>
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
        <span>{alerts.length} alert{alerts.length !== 1 ? "s" : ""} on {selectedDate}</span>
        <span>{selectedDate === new Date().toLocaleDateString("en-CA") ? "Live stream active" : "Historical view"}</span>
      </div>
    </div>
  );
}

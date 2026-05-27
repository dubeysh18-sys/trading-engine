"use client";

import { useState, useRef, useEffect } from "react";
import { ExternalLink, Settings2, Zap } from "lucide-react";
import { useWebSocket } from "./WebSocketListener";
import VerdictPill from "./VerdictPill";
import { fetchAlertsByDate, Alert } from "../lib/api";

/* ── helpers ──────────────────────────────────────────────────────── */
function fmt(v: number | null | undefined, d = 2) {
  return v == null ? "—" : v.toFixed(d);
}
function formatTime(s: string | null) {
  if (!s) return "—";
  return s;
}
function kiteLink(stock: string) {
  return `https://kite.zerodha.com/chart/web/ciq/NSE/${stock}/EQ`;
}

/* ── Capital / Leverage options ───────────────────────────────────── */
const CAPITAL_OPTIONS = [
  { label: "₹25,000",    value: 25000 },
  { label: "₹50,000",    value: 50000 },
  { label: "₹1,00,000",  value: 100000 },
  { label: "₹2,00,000",  value: 200000 },
  { label: "₹5,00,000",  value: 500000 },
];
const LEVERAGE_OPTIONS = [
  { label: "3× Leverage", value: 3 },
  { label: "4× Leverage", value: 4 },
  { label: "5× Leverage", value: 5 },
];

/* ── Dropdown component ───────────────────────────────────────────── */
function StyledSelect<T extends string | number>({
  options, value, onChange, id,
}: {
  options: { label: string; value: T }[];
  value: T;
  onChange: (v: T) => void;
  id: string;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => {
        const val = e.target.value;
        onChange((typeof value === 'number' ? Number(val) : val) as T);
      }}
      style={{
        background: "#0f1923",
        border: "1px solid #1e2d45",
        borderRadius: 6,
        color: "#94a3b8",
        fontSize: 11,
        fontWeight: 600,
        padding: "4px 24px 4px 10px",
        cursor: "pointer",
        appearance: "none",
        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%2394a3b8' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")`,
        backgroundRepeat: "no-repeat",
        backgroundPosition: "right 8px center",
        outline: "none",
        minWidth: 110,
      }}
    >
      {options.map((o) => (
        <option key={o.value.toString()} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

/* ── Main component ───────────────────────────────────────────────── */
export default function AlertFeed({ selectedDate }: { selectedDate: string }) {
  const { alerts: wsAlerts, isConnected } = useWebSocket();
  const [dbAlerts, setDbAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(false);
  const [capital, setCapital] = useState(50000);
  const [leverage, setLeverage] = useState(4);
  const [filter, setFilter] = useState<"ALL" | "ENTER" | "WAIT" | "SKIP">("ALL");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);

  // Check if selectedDate is today in local time YYYY-MM-DD
  const todayStr = new Date().toLocaleDateString("en-CA");
  const isToday = selectedDate === todayStr;

  // Fetch historical alerts if not today
  useEffect(() => {
    if (!isToday) {
      setLoading(true);
      fetchAlertsByDate(selectedDate).then((data) => {
        setDbAlerts(data);
        setLoading(false);
      });
    }
  }, [selectedDate, isToday]);

  // Dismiss settings panel on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setSettingsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const displayedAlerts = isToday ? wsAlerts : dbAlerts;

  const filteredAlerts = displayedAlerts.filter((alert) => {
    if (filter === "ALL") return true;
    return alert.verdict === filter;
  });

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
            ALERT FEED
          </h2>
          <span
            style={{
              background: !isToday ? "rgba(148,163,184,0.15)" : (isConnected ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)"),
              color: !isToday ? "#94a3b8" : (isConnected ? "var(--accent-green)" : "var(--accent-red)"),
              fontSize: 10,
              fontWeight: 700,
              padding: "1px 7px",
              letterSpacing: "0.04em",
              borderRadius: 4,
            }}
          >
            {!isToday ? "HISTORICAL" : (isConnected ? "LIVE" : "OFFLINE")}
          </span>
        </div>

        {/* Action Controls */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, position: "relative" }}>
          {/* Settings Trigger */}
          <button
            onClick={() => setSettingsOpen(!settingsOpen)}
            style={{
              background: settingsOpen ? "#1e2d45" : "transparent",
              border: "none",
              color: settingsOpen ? "#f8fafc" : "#94a3b8",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 5,
              fontSize: 11,
              fontWeight: 600,
              padding: "6px 10px",
              borderRadius: 6,
              transition: "all 0.15s",
            }}
          >
            <Settings2 size={13} />
            Settings
          </button>

          {/* Settings Popover */}
          {settingsOpen && (
            <div
              ref={settingsRef}
              style={{
                position: "absolute",
                top: 36,
                right: 0,
                background: "#0f172a",
                border: "1px solid #1e2d45",
                borderRadius: 8,
                boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.5)",
                padding: 16,
                zIndex: 100,
                display: "flex",
                flexDirection: "column",
                gap: 12,
                minWidth: 260,
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label htmlFor="capital-select" style={{ fontSize: 10, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>
                  Capital allocation
                </label>
                <StyledSelect id="capital-select" options={CAPITAL_OPTIONS} value={capital} onChange={setCapital} />
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label htmlFor="leverage-select" style={{ fontSize: 10, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>
                  Intraday Leverage
                </label>
                <StyledSelect id="leverage-select" options={LEVERAGE_OPTIONS} value={leverage} onChange={setLeverage} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Filter Tabs */}
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid #1e2d45",
          background: "#0a0e17",
          padding: "4px 8px",
          gap: 4,
        }}
      >
        {(["ALL", "ENTER", "WAIT", "SKIP"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setFilter(t)}
            style={{
              background: filter === t ? "#0f1923" : "transparent",
              border: filter === t ? "1px solid #1e2d45" : "1px solid transparent",
              color: filter === t ? "#f8fafc" : "#64748b",
              borderRadius: 6,
              padding: "4px 12px",
              fontSize: 11,
              fontWeight: 600,
              cursor: "pointer",
              transition: "all 0.15s",
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Alert Feed Body */}
      <div className="card-body" style={{ flex: 1, overflowY: "auto" }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: "center", color: "#4b5563" }}>
            <div style={{ fontSize: 13 }}>Loading historical alerts...</div>
          </div>
        ) : filteredAlerts.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "#4b5563" }}>
            <div style={{ fontSize: 28, marginBottom: 10 }}>📡</div>
            <div style={{ fontSize: 13 }}>Waiting for live Chartink alerts...</div>
            <div style={{ fontSize: 11, marginTop: 6, color: "#64748b" }}>
              Alerts evaluated by the rule engine will appear here instantly.
            </div>
          </div>
        ) : (
          <table className="data-table" style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "8px 12px", fontSize: 11, color: "#64748b" }}>Time</th>
                <th style={{ textAlign: "left", padding: "8px 12px", fontSize: 11, color: "#64748b" }}>Stock</th>
                <th style={{ textAlign: "left", padding: "8px 12px", fontSize: 11, color: "#64748b" }}>Verdict</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, color: "#64748b" }}>Trigger ₹</th>
                <th style={{ textAlign: "left", padding: "8px 12px", fontSize: 11, color: "#64748b" }}>Anatomy / Reason</th>
                <th style={{ textAlign: "center", padding: "8px 12px", fontSize: 11, color: "#64748b" }}>Chart</th>
              </tr>
            </thead>
            <tbody>
              {filteredAlerts.map((alert, idx) => (
                <tr key={idx} style={{ borderBottom: "1px solid #0f1923" }}>
                  <td style={{ padding: "8px 12px", fontSize: 11, color: "#64748b" }}>
                    {formatTime(alert.trigger_time)}
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    <span style={{ fontWeight: 600, color: "#e2e8f0", fontFamily: "monospace" }}>
                      {alert.stock}
                    </span>
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    <VerdictPill verdict={alert.verdict} reason={alert.reason} />
                  </td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontFamily: "monospace", color: "#cbd5e1" }}>
                    {fmt(alert.trigger_price)}
                  </td>
                  <td style={{ padding: "8px 12px", fontSize: 11, color: "#94a3b8", maxWidth: 220, whiteSpace: "normal", wordBreak: "break-word" }}>
                    {alert.reason || "—"}
                  </td>
                  <td style={{ padding: "8px 12px", textAlign: "center" }}>
                    <a
                      href={kiteLink(alert.stock)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="kite-btn"
                      style={{
                        color: "#3b82f6",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 3,
                        fontSize: 10,
                        textDecoration: "none",
                        padding: "2px 6px",
                        borderRadius: 4,
                        border: "1px solid rgba(59,130,246,0.2)",
                        transition: "all 0.15s",
                      }}
                    >
                      Kite <ExternalLink size={9} />
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

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
        <span>{filteredAlerts.length} alert{filteredAlerts.length !== 1 ? "s" : ""} today</span>
        <span>Connected to WebSocket</span>
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { ExternalLink, RefreshCw, Zap, Settings2 } from "lucide-react";
import { fetchAlerts, API, type Alert, type LivePrice } from "@/lib/api";
import VerdictPill from "./VerdictPill";

/* ── helpers ──────────────────────────────────────────────────────── */
function fmt(v: number | null | undefined, d = 2) {
  return v == null ? "—" : v.toFixed(d);
}
function calcQty(entry: number | null, capital: number, leverage: number) {
  if (!entry || entry <= 0) return null;
  return Math.floor((capital * leverage) / entry);
}
function calcExposure(qty: number | null, entry: number | null) {
  if (!qty || !entry) return null;
  return (qty * entry).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}
function formatTime(s: string | null) {
  if (!s) return "—";
  try {
    const d = new Date(`1970-01-01 ${s.toLowerCase().trim()}`);
    if (!isNaN(d.getTime()))
      return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {}
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

/* ── P&L badge ────────────────────────────────────────────────────── */
function PnlBadge({ pnl, status }: { pnl: number | null; status: string }) {
  const color =
    status === "PROFIT" ? "var(--accent-green)"
    : status === "LOSS"   ? "var(--accent-red)"
    : status === "ACTIVE" ? "#60a5fa"
    :                        "#4b5563";

  return (
    <span style={{ color, fontFamily: "JetBrains Mono, monospace", fontWeight: 700, fontSize: 12 }}>
      {pnl == null ? "—" : `${pnl > 0 ? "+" : ""}${pnl.toFixed(2)}%`}
    </span>
  );
}

/* ── Main component ───────────────────────────────────────────────── */
export default function AlertFeed() {
  const [alerts, setAlerts]         = useState<Alert[]>([]);
  const [livePrices, setLivePrices] = useState<Record<number, LivePrice>>({});
  const [capital, setCapital]       = useState(50000);
  const [leverage, setLeverage]     = useState(4);
  const [filter, setFilter]         = useState<"ALL" | "ENTER" | "WAIT" | "SKIP">("ALL");
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [lastFetch, setLastFetch]   = useState<Date | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);

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

  const refresh = useCallback(async () => {
    try {
      const alertData = await fetchAlerts();
      setAlerts(alertData);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load alerts");
    }

    setLastFetch(new Date());
    setLoading(false);
  }, []);

  // Load alerts once on mount
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Real-time SSE for instant webhook alerts & live prices
  useEffect(() => {
    const sse = new EventSource(`${API}/api/alerts/stream`);
    
    sse.addEventListener("new_alert", (e) => {
      try {
        const newAlert = JSON.parse(e.data);
        setAlerts((prev) => {
          // Prevent duplicates if API fetch already got it
          if (prev.some(a => a.id === newAlert.id)) return prev;
          return [newAlert, ...prev];
        });
        refresh();
      } catch (err) {
        console.error("SSE parse error", err);
      }
    });

    sse.addEventListener("price_update", (e) => {
      try {
        const data = JSON.parse(e.data);
        setLivePrices((prev) => ({
          ...prev,
          [data.alert_id]: {
            ...prev[data.alert_id],
            alert_id: data.alert_id,
            ltp: data.ltp,
            pnl_pct: data.floating_pnl_pct,
            status: data.status,
          }
        }));
      } catch (err) {
        console.error("SSE price_update parse error", err);
      }
    });

    return () => sse.close();
  }, [refresh]);

  const enterCount = alerts.filter((a) => a.verdict === "ENTER").length;
  const skipCount  = alerts.filter((a) => a.verdict === "SKIP").length;
  const waitCount  = alerts.filter((a) => a.verdict === "WAIT").length;

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%" }}>

      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="card-header" style={{ flexWrap: "wrap", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Zap size={16} color="var(--accent-yellow)" />
          <span style={{ fontWeight: 600, fontSize: 14, color: "#e2e8f0" }}>Live Alert Feed</span>
          <span style={{
            background: "rgba(251,191,36,0.12)", color: "var(--accent-yellow)",
            border: "1px solid rgba(251,191,36,0.25)", borderRadius: 9999,
            fontSize: 10, fontWeight: 700, padding: "1px 7px",
          }}>LIVE</span>
          <span style={{ fontSize: 11, color: "#4b5563" }}>
            <span style={{ color: "var(--accent-green)" }}>↑{enterCount}</span>
            {" · "}
            <span style={{ color: "var(--accent-yellow)" }}>⏸{waitCount}</span>
            {" · "}
            <span style={{ color: "var(--accent-red)" }}>✕{skipCount}</span>
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {/* Filters and options */}
          <StyledSelect
            id="filter-select"
            options={[
              { label: "All Signals", value: "ALL" },
              { label: "🟢 Enter Only", value: "ENTER" },
              { label: "🟡 Wait Only", value: "WAIT" },
              { label: "🔴 Skip Only", value: "SKIP" },
            ]}
            value={filter}
            onChange={setFilter}
          />
          <StyledSelect
            id="capital-select"
            options={CAPITAL_OPTIONS}
            value={capital}
            onChange={setCapital}
          />
          <StyledSelect
            id="leverage-select"
            options={LEVERAGE_OPTIONS}
            value={leverage}
            onChange={setLeverage}
          />

          {/* Refresh timestamp */}
          <button
            onClick={refresh}
            style={{
              background: "transparent", border: "none", cursor: "pointer",
              color: "#4b5563", display: "flex", alignItems: "center",
              gap: 4, fontSize: 11, padding: "4px 6px", borderRadius: 4,
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#94a3b8")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "#4b5563")}
          >
            <RefreshCw size={12} />
            {lastFetch?.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true })}
          </button>
        </div>
      </div>

      {/* ── Position sizing hint bar ────────────────────────────── */}
      {enterCount > 0 && (
        <div style={{
          padding: "6px 16px",
          background: "rgba(34,197,94,0.05)",
          borderBottom: "1px solid rgba(34,197,94,0.12)",
          fontSize: 11,
          color: "#94a3b8",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}>
          <span style={{ color: "var(--accent-green)", fontWeight: 600 }}>📐 Position Sizing</span>
          <span>Capital: <strong style={{ color: "#e2e8f0" }}>
            {CAPITAL_OPTIONS.find(c => c.value === capital)?.label}
          </strong></span>
          <span>·</span>
          <span>Leverage: <strong style={{ color: "#e2e8f0" }}>{leverage}×</strong></span>
          <span>·</span>
          <span>Effective: <strong style={{ color: "#60a5fa" }}>
            ₹{(capital * leverage).toLocaleString("en-IN")}
          </strong></span>
        </div>
      )}

      {/* ── Table ──────────────────────────────────────────────── */}
      <div className="card-body" style={{ flex: 1, padding: 0 }}>
        {(() => {
          const visibleAlerts = alerts.filter(a => filter === "ALL" || a.verdict === filter);

          if (loading && alerts.length === 0) {
            return (
              <div style={{ padding: 40, textAlign: "center", color: "#4b5563" }}>
                <RefreshCw size={20} style={{ margin: "0 auto 10px", display: "block", opacity: 0.4 }} />
                <div style={{ fontSize: 13 }}>Loading alerts...</div>
              </div>
            );
          }
          
          if (error) {
            return (
              <div style={{
                margin: 16, padding: "12px 16px",
                background: "rgba(255,77,77,0.08)", border: "1px solid rgba(255,77,77,0.2)",
                borderRadius: 8, color: "var(--accent-red)", fontSize: 12,
              }}>
                ⚠ {error}
              </div>
            );
          }

          if (alerts.length === 0) {
            return (
              <div style={{ padding: 40, textAlign: "center", color: "#4b5563" }}>
                <div style={{ fontSize: 28, marginBottom: 10 }}>📡</div>
                <div style={{ fontSize: 13 }}>Waiting for Chartink alerts...</div>
                <div style={{ fontSize: 11, marginTop: 6 }}>Alerts appear within seconds of your scan firing.</div>
              </div>
            );
          }

          if (visibleAlerts.length === 0) {
            return (
              <div style={{ padding: 40, textAlign: "center", color: "#4b5563" }}>
                <div style={{ fontSize: 13 }}>No {filter.toLowerCase()} signals today.</div>
              </div>
            );
          }

          return (
            <table className="data-table" style={{ tableLayout: "fixed", width: "100%" }}>
            <colgroup>
              <col style={{ width: 52 }} />
              <col style={{ width: 90 }} />
              <col style={{ width: 90 }} />
              <col style={{ width: 70 }} />  {/* Entry */}
              <col style={{ width: 78 }} />  {/* Qty / Exposure */}
              <col style={{ width: 70 }} />  {/* Target */}
              <col style={{ width: 68 }} />  {/* SL */}
              <col style={{ width: 90 }} />  {/* Live / P&L */}
              <col style={{ width: 50 }} />  {/* Chart */}
            </colgroup>
            <thead>
              <tr>
                <th>Time</th>
                <th>Stock</th>
                <th>Verdict</th>
                <th style={{ textAlign: "right" }}>Entry ₹</th>
                <th style={{ textAlign: "right" }}>Qty / ₹</th>
                <th style={{ textAlign: "right" }}>Target</th>
                <th style={{ textAlign: "right" }}>SL</th>
                <th style={{ textAlign: "right" }}>Live / P&L</th>
                <th>Chart</th>
              </tr>
            </thead>
            <tbody>
              {visibleAlerts.map((alert) => {
                const lp     = livePrices[alert.id] ?? null;
                const qty    = alert.verdict === "ENTER" ? calcQty(alert.entry, capital, leverage) : null;
                const exp    = calcExposure(qty, alert.entry);
                const isEnter = alert.verdict === "ENTER";

                return (
                  <tr
                    key={alert.id}
                    className="animate-fade-in"
                    style={isEnter ? {
                      borderLeft: "3px solid var(--accent-green)",
                      background: "rgba(34,197,94,0.04)",
                    } : {}}
                  >
                    {/* Time */}
                    <td style={{ color: "#64748b", fontSize: 11, paddingLeft: isEnter ? 13 : 16 }}>
                      {formatTime(alert.trigger_time)}
                    </td>

                    {/* Stock */}
                    <td>
                      <div style={{
                        fontWeight: 700, fontSize: 12, color: "#e2e8f0",
                        fontFamily: "JetBrains Mono, monospace",
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      }}>
                        {alert.stock}
                      </div>
                      {alert.scan_name && (
                        <div style={{ fontSize: 10, color: "#4b5563", marginTop: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                          {alert.scan_name}
                        </div>
                      )}
                    </td>

                    {/* Verdict */}
                    <td>
                      <div title={alert.verdict_reason ?? ""}>
                        <VerdictPill verdict={alert.verdict} reason={null} />
                      </div>
                      {alert.verdict_reason && (
                        <div style={{
                          fontSize: 9.5, color: "#4b5563", marginTop: 2,
                          maxWidth: 86, lineHeight: 1.3,
                          overflow: "hidden",
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                        }}>
                          {alert.verdict_reason}
                        </div>
                      )}
                    </td>

                    {/* Entry */}
                    <td style={{ textAlign: "right" }}>
                      {alert.entry ? (
                        <span style={{ color: "var(--accent-green)", fontFamily: "JetBrains Mono, monospace", fontWeight: 700, fontSize: 12 }}>
                          {fmt(alert.entry)}
                        </span>
                      ) : (
                        <span style={{ color: "#374151" }}>—</span>
                      )}
                    </td>

                    {/* Qty / Exposure */}
                    <td style={{ textAlign: "right" }}>
                      {qty !== null ? (
                        <div>
                          <div style={{ fontFamily: "JetBrains Mono, monospace", fontWeight: 700, fontSize: 12, color: "#e2e8f0" }}>
                            {qty} <span style={{ fontSize: 10, fontWeight: 400, color: "#4b5563" }}>sh</span>
                          </div>
                          <div style={{ fontSize: 10, color: "#4b5563" }}>₹{exp}</div>
                        </div>
                      ) : (
                        <span style={{ color: "#374151" }}>—</span>
                      )}
                    </td>

                    {/* Target */}
                    <td style={{ textAlign: "right" }}>
                      {alert.target ? (
                        <span style={{ color: "#60a5fa", fontFamily: "JetBrains Mono, monospace", fontSize: 12 }}>
                          {fmt(alert.target)}
                        </span>
                      ) : (
                        <span style={{ color: "#374151" }}>—</span>
                      )}
                    </td>

                    {/* SL */}
                    <td style={{ textAlign: "right" }}>
                      {alert.stop_loss ? (
                        <span style={{ color: "var(--accent-red)", fontFamily: "JetBrains Mono, monospace", fontSize: 12 }}>
                          {fmt(alert.stop_loss)}
                        </span>
                      ) : (
                        <span style={{ color: "#374151" }}>—</span>
                      )}
                    </td>

                    {/* Live / P&L */}
                    <td style={{ textAlign: "right" }}>
                      {lp ? (
                        <div>
                          <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "#e2e8f0", fontWeight: 600 }}>
                            ₹{lp.ltp?.toFixed(2) ?? "—"}
                          </div>
                          <PnlBadge pnl={lp.pnl_pct} status={lp.status} />
                        </div>
                      ) : (
                        <span style={{ color: "#374151", fontSize: 11 }}>—</span>
                      )}
                    </td>

                    {/* Chart */}
                    <td>
                      <a
                        href={kiteLink(alert.stock)}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          color: "#3b82f6", display: "inline-flex", alignItems: "center",
                          gap: 3, fontSize: 11, textDecoration: "none",
                          padding: "2px 6px", borderRadius: 4,
                          border: "1px solid rgba(59,130,246,0.2)",
                          transition: "all 0.15s",
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(59,130,246,0.12)"; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                      >
                        Kite <ExternalLink size={9} />
                      </a>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          );
        })()}
      </div>

      {/* ── Footer ─────────────────────────────────────────────── */}
      <div style={{
        padding: "7px 16px", borderTop: "1px solid #1e2d45",
        color: "#4b5563", fontSize: 11,
        display: "flex", justifyContent: "space-between",
      }}>
        <span>{alerts.length} alert{alerts.length !== 1 ? "s" : ""} today</span>
        <span>Auto-refreshes every 10s</span>
      </div>
    </div>
  );
}

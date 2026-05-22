"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { ExternalLink, RefreshCw, Zap, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { fetchAlerts, fetchLivePrices, type Alert, type LivePrice, API } from "@/lib/api";
import VerdictPill from "./VerdictPill";

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmt(val: number | null | undefined, decimals = 2): string {
  if (val === null || val === undefined) return "—";
  return val.toFixed(decimals);
}

function fmtPct(val: number | null | undefined): string {
  if (val === null || val === undefined) return "—";
  return (val >= 0 ? "+" : "") + val.toFixed(2) + "%";
}

function kiteLink(stock: string): string {
  return `https://kite.zerodha.com/chart/web/ciq/NSE/${stock}/EQ`;
}

function formatTime(timeStr: string | null): string {
  if (!timeStr) return "—";
  try {
    const lower = timeStr.toLowerCase().trim();
    const d = new Date(`1970-01-01 ${lower}`);
    if (!isNaN(d.getTime())) {
      return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
    }
  } catch {}
  return timeStr;
}

// Progress bar between SL and Target showing where LTP sits
function TradeProgressBar({
  entry, target, stopLoss, ltp, status,
}: {
  entry: number | null; target: number | null; stopLoss: number | null;
  ltp: number | null; status: string;
}) {
  if (!entry || !target || !stopLoss) return null;

  const range = target - stopLoss;
  const ltpVal = ltp ?? entry;
  const raw = ((ltpVal - stopLoss) / range) * 100;
  const pct = Math.max(0, Math.min(100, raw));

  const isProfit = status === "PROFIT" || ltpVal >= target;
  const isLoss   = status === "LOSS"   || ltpVal <= stopLoss;
  const color    = isProfit ? "var(--accent-green)" : isLoss ? "var(--accent-red)" : "#3b82f6";

  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "#4b5563", marginBottom: 3 }}>
        <span>SL {fmt(stopLoss)}</span>
        <span style={{ color: "#94a3b8" }}>Entry {fmt(entry)}</span>
        <span>T {fmt(target)}</span>
      </div>
      <div style={{ position: "relative", height: 5, borderRadius: 4, background: "rgba(255,255,255,0.07)" }}>
        <div style={{
          position: "absolute", left: 0, top: 0, height: "100%", width: `${pct}%`,
          borderRadius: 4, background: color,
          transition: "width 0.8s cubic-bezier(0.4,0,0.2,1)",
        }} />
        {/* Entry marker */}
        <div style={{
          position: "absolute",
          left: `${((entry - stopLoss) / range) * 100}%`,
          top: -3, width: 2, height: 11,
          background: "#94a3b8", borderRadius: 1,
          transform: "translateX(-50%)",
        }} />
      </div>
    </div>
  );
}

// Live price badge for an ENTER alert
function LivePriceBadge({ data }: { data: LivePrice | undefined }) {
  if (!data) return <span style={{ color: "#4b5563", fontSize: 11 }}>—</span>;

  const { ltp, pnl_pct, status } = data;
  const color =
    status === "PROFIT" ? "var(--accent-green)" :
    status === "LOSS"   ? "var(--accent-red)"   :
    status === "ACTIVE" ? "#3b82f6"              : "#4b5563";

  const Icon =
    (pnl_pct ?? 0) > 0 ? TrendingUp :
    (pnl_pct ?? 0) < 0 ? TrendingDown : Minus;

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 1 }}>
      <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color, fontWeight: 600 }}>
        {ltp ? `₹${fmt(ltp)}` : "—"}
      </span>
      {pnl_pct !== null && (
        <span style={{ display: "flex", alignItems: "center", gap: 2, fontSize: 10, color }}>
          <Icon size={10} />
          {fmtPct(pnl_pct)}
        </span>
      )}
      {status === "PROFIT" && <span style={{ fontSize: 9, color: "var(--accent-green)", fontWeight: 700, letterSpacing: "0.06em" }}>TARGET HIT ✓</span>}
      {status === "LOSS"   && <span style={{ fontSize: 9, color: "var(--accent-red)",   fontWeight: 700, letterSpacing: "0.06em" }}>SL HIT ✗</span>}
      {status === "FLAT"   && <span style={{ fontSize: 9, color: "#94a3b8", fontWeight: 700, letterSpacing: "0.06em" }}>SQUARED OFF</span>}
    </div>
  );
}

// ── Row component ──────────────────────────────────────────────────────────────

function AlertRow({ alert, livePrice }: { alert: Alert; livePrice: LivePrice | undefined }) {
  const isEnter = alert.verdict === "ENTER";
  const isWait  = alert.verdict === "WAIT";
  const isSkip  = alert.verdict === "SKIP";
  const dimmed  = isSkip || alert.verdict === "ERROR";

  const rowStyle: React.CSSProperties = {
    opacity: dimmed ? 0.6 : 1,
    borderLeft: isEnter
      ? "3px solid var(--accent-green)"
      : isWait
      ? "3px solid var(--accent-yellow)"
      : "3px solid transparent",
    transition: "opacity 0.2s",
  };

  return (
    <tr className="animate-fade-in" style={rowStyle}>
      {/* Time */}
      <td style={{ color: "#94a3b8", fontSize: 11, paddingLeft: isEnter ? 9 : 12 }}>
        {formatTime(alert.trigger_time)}
      </td>

      {/* Stock */}
      <td>
        <div style={{ fontWeight: 600, color: isEnter ? "#e2e8f0" : "#94a3b8", fontFamily: "JetBrains Mono, monospace", fontSize: 12 }}>
          {alert.stock}
        </div>
        {alert.scan_name && (
          <div style={{ fontSize: 9, color: "#4b5563", marginTop: 1 }}>{alert.scan_name}</div>
        )}
      </td>

      {/* Verdict + reason */}
      <td style={{ maxWidth: 180 }}>
        <VerdictPill verdict={alert.verdict} reason={alert.verdict_reason} />
        {alert.verdict_reason && (
          <div style={{ fontSize: 10, color: "#4b5563", marginTop: 2, whiteSpace: "normal", lineHeight: 1.3 }}>
            {alert.verdict_reason}
          </div>
        )}
      </td>

      {/* Entry */}
      <td style={{ textAlign: "right", fontFamily: "JetBrains Mono, monospace" }}>
        {alert.entry
          ? <span style={{ color: "var(--accent-green)", fontWeight: 600 }}>{fmt(alert.entry)}</span>
          : <span style={{ color: "#4b5563" }}>—</span>}
      </td>

      {/* Target */}
      <td style={{ textAlign: "right", fontFamily: "JetBrains Mono, monospace" }}>
        {alert.target
          ? <span style={{ color: "#60a5fa" }}>{fmt(alert.target)}</span>
          : <span style={{ color: "#4b5563" }}>—</span>}
      </td>

      {/* SL */}
      <td style={{ textAlign: "right", fontFamily: "JetBrains Mono, monospace" }}>
        {alert.stop_loss
          ? <span style={{ color: "var(--accent-red)" }}>{fmt(alert.stop_loss)}</span>
          : <span style={{ color: "#4b5563" }}>—</span>}
      </td>

      {/* VWAP */}
      <td style={{ textAlign: "right", color: "#94a3b8", fontFamily: "JetBrains Mono, monospace", fontSize: 11 }}>
        {fmt(alert.vwap)}
      </td>

      {/* Live Price / P&L (ENTER only) */}
      <td style={{ textAlign: "right", minWidth: 90 }}>
        {isEnter ? (
          <div>
            <LivePriceBadge data={livePrice} />
            <TradeProgressBar
              entry={alert.entry}
              target={alert.target}
              stopLoss={alert.stop_loss}
              ltp={livePrice?.ltp ?? null}
              status={livePrice?.status ?? "PENDING"}
            />
          </div>
        ) : (
          <span style={{ color: "#4b5563", fontSize: 11 }}>—</span>
        )}
      </td>

      {/* Chart */}
      <td>
        <a
          href={kiteLink(alert.stock)}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            color: "#3b82f6", display: "inline-flex", alignItems: "center", gap: 3,
            fontSize: 11, textDecoration: "none", padding: "2px 6px", borderRadius: 4,
            border: "1px solid rgba(59,130,246,0.25)", transition: "all 0.15s",
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(59,130,246,0.12)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
        >
          Kite <ExternalLink size={10} />
        </a>
      </td>
    </tr>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────────

export default function AlertFeed({ selectedDate }: { selectedDate: string }) {
  const [alerts, setAlerts]         = useState<Alert[]>([]);
  const [livePrices, setLivePrices] = useState<LivePrice[]>([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [lastFetch, setLastFetch]   = useState<Date | null>(null);
  const lpTimerRef                  = useRef<ReturnType<typeof setInterval> | null>(null);

  const isToday = selectedDate === new Date().toLocaleDateString("en-CA");

  // ── Pull all alerts ──────────────────────────────────────────────────────────
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

  // ── Pull live prices for ENTER alerts ───────────────────────────────────────
  const refreshLivePrices = useCallback(async () => {
    try {
      const data = await fetchLivePrices(selectedDate);
      setLivePrices(data);
    } catch {
      // Silent — live prices are best-effort
    }
  }, [selectedDate]);

  // ── Initial load + SSE ───────────────────────────────────────────────────────
  useEffect(() => {
    refresh();

    if (!isToday) return;

    // SSE for instant new-alert pushes
    const evtSrc = new EventSource(`${API}/api/alerts/stream`);
    evtSrc.addEventListener("new_alert", (ev) => {
      try {
        const newAlert = JSON.parse(ev.data);
        setAlerts((prev) => {
          // avoid duplicates
          if (prev.some((a) => a.id === newAlert.id)) return prev;
          return [newAlert, ...prev];
        });
        setLastFetch(new Date());
      } catch {}
    });
    evtSrc.onerror = () => evtSrc.close();

    return () => evtSrc.close();
  }, [refresh, isToday]);

  // ── Live price polling every 30 s (today only) ───────────────────────────────
  useEffect(() => {
    if (lpTimerRef.current) clearInterval(lpTimerRef.current);
    refreshLivePrices();

    if (isToday) {
      lpTimerRef.current = setInterval(refreshLivePrices, 30_000);
    }
    return () => {
      if (lpTimerRef.current) clearInterval(lpTimerRef.current);
    };
  }, [refreshLivePrices, isToday]);

  // ── Derived counts ───────────────────────────────────────────────────────────
  const enterCount = alerts.filter((a) => a.verdict === "ENTER").length;
  const skipCount  = alerts.filter((a) => a.verdict === "SKIP").length;
  const waitCount  = alerts.filter((a) => a.verdict === "WAIT").length;
  const errorCount = alerts.filter((a) => a.verdict === "ERROR").length;

  // Sort: ENTER first, then WAIT, then SKIP/ERROR — within each group by time desc
  const verdictOrder: Record<string, number> = { ENTER: 0, WAIT: 1, SKIP: 2, ERROR: 3, PENDING: 4 };
  const sortedAlerts = [...alerts].sort((a, b) => {
    const vDiff = (verdictOrder[a.verdict] ?? 9) - (verdictOrder[b.verdict] ?? 9);
    if (vDiff !== 0) return vDiff;
    return new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime();
  });

  const liveMap = Object.fromEntries(livePrices.map((lp) => [lp.alert_id, lp]));

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header */}
      <div className="card-header">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Zap size={16} color="var(--accent-yellow)" />
          <span style={{ fontWeight: 600, fontSize: 14, color: "#e2e8f0" }}>Live Alert Feed</span>
          <span style={{
            background: "rgba(251,191,36,0.12)", color: "var(--accent-yellow)",
            border: "1px solid rgba(251,191,36,0.25)", borderRadius: 9999,
            fontSize: 10, fontWeight: 700, padding: "1px 7px", letterSpacing: "0.04em",
          }}>LIVE</span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ display: "flex", gap: 10, fontSize: 11 }}>
            {enterCount > 0 && <span style={{ color: "var(--accent-green)", fontWeight: 600 }}>↑ {enterCount} ENTER</span>}
            {waitCount  > 0 && <span style={{ color: "var(--accent-yellow)" }}>⏸ {waitCount} WAIT</span>}
            {skipCount  > 0 && <span style={{ color: "var(--accent-red)" }}>✕ {skipCount} SKIP</span>}
            {errorCount > 0 && <span style={{ color: "#4b5563" }}>⚠ {errorCount} ERR</span>}
            {alerts.length === 0 && <span style={{ color: "#4b5563" }}>No alerts yet</span>}
          </div>
          <button
            onClick={() => { refresh(); refreshLivePrices(); }}
            style={{
              background: "transparent", border: "none", cursor: "pointer",
              color: "#4b5563", display: "flex", alignItems: "center", gap: 4,
              fontSize: 11, padding: "2px 6px", borderRadius: 4, transition: "color 0.15s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#94a3b8")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "#4b5563")}
            title="Refresh"
          >
            <RefreshCw size={12} />
            {lastFetch ? lastFetch.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true }) : ""}
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
          <div style={{ padding: 20, margin: 16, background: "rgba(255,77,77,0.08)", border: "1px solid rgba(255,77,77,0.2)", borderRadius: 8, color: "var(--accent-red)", fontSize: 12 }}>
            ⚠ {error}
          </div>
        ) : alerts.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "#4b5563" }}>
            <div style={{ fontSize: 28, marginBottom: 10 }}>📡</div>
            <div style={{ fontSize: 13 }}>Waiting for Chartink alerts...</div>
            <div style={{ fontSize: 11, marginTop: 6 }}>Alerts will appear here within seconds of your scan firing.</div>
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
                <th style={{ textAlign: "right" }}>SL ₹</th>
                <th style={{ textAlign: "right" }}>VWAP</th>
                <th style={{ textAlign: "right" }}>Live / P&L</th>
                <th>Chart</th>
              </tr>
            </thead>
            <tbody>
              {sortedAlerts.map((alert) => (
                <AlertRow
                  key={alert.id}
                  alert={alert}
                  livePrice={liveMap[alert.id]}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Footer */}
      <div style={{
        padding: "8px 16px", borderTop: "1px solid #1e2d45",
        color: "#4b5563", fontSize: 11, display: "flex", justifyContent: "space-between",
      }}>
        <span>
          {alerts.length} alert{alerts.length !== 1 ? "s" : ""} on {selectedDate}
          {enterCount > 0 && isToday && (
            <span style={{ marginLeft: 8, color: "#3b82f6" }}>
              · Live prices refresh every 30s
            </span>
          )}
        </span>
        <span>{isToday ? "Live stream active" : "Historical view"}</span>
      </div>
    </div>
  );
}

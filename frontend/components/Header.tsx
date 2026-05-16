"use client";

import { useEffect, useState, useCallback } from "react";
import { Activity, RefreshCw, TrendingUp, TrendingDown } from "lucide-react";
import { fetchNiftyStatus, type NiftyStatus } from "@/lib/api";

function useISTClock() {
  const [time, setTime] = useState("");
  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setTime(
        now.toLocaleTimeString("en-IN", {
          timeZone: "Asia/Kolkata",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        })
      );
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);
  return time;
}

function useISTDate() {
  const [date, setDate] = useState("");
  useEffect(() => {
    const d = new Date().toLocaleDateString("en-IN", {
      timeZone: "Asia/Kolkata",
      weekday: "short",
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
    setDate(d);
  }, []);
  return date;
}

export default function Header() {
  const time = useISTClock();
  const date = useISTDate();
  const [nifty, setNifty] = useState<NiftyStatus | null>(null);
  const [lastRefresh, setLastRefresh] = useState("");

  const refreshNifty = useCallback(async () => {
    try {
      const data = await fetchNiftyStatus();
      setNifty(data);
      setLastRefresh(
        new Date().toLocaleTimeString("en-IN", {
          timeZone: "Asia/Kolkata",
          hour: "2-digit",
          minute: "2-digit",
          hour12: true,
        })
      );
    } catch {
      // silently fail — keep last known state
    }
  }, []);

  useEffect(() => {
    refreshNifty();
    const id = setInterval(refreshNifty, 15_000); // every 15s
    return () => clearInterval(id);
  }, [refreshNifty]);

  const isBullish = nifty?.is_bullish;
  const niftyLtp  = nifty?.ltp;
  const niftyVwap = nifty?.vwap;

  return (
    <header
      style={{
        background: "linear-gradient(135deg, #111827 0%, #0f1e35 100%)",
        borderBottom: "1px solid #1e2d45",
        padding: "0 24px",
        height: "60px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        position: "sticky",
        top: 0,
        zIndex: 50,
      }}
    >
      {/* ── Left: Logo ─────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div
          style={{
            width: 32,
            height: 32,
            background: "linear-gradient(135deg, #3b82f6, #8b5cf6)",
            borderRadius: 8,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Activity size={18} color="#fff" />
        </div>
        <div>
          <div
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: "#e2e8f0",
              letterSpacing: "-0.02em",
              lineHeight: 1.2,
            }}
          >
            Trading Rule Engine
          </div>
          <div style={{ fontSize: 11, color: "#4b5563", letterSpacing: "0.06em" }}>
            INTRADAY SCANNER + EOD BACKTESTER
          </div>
        </div>
      </div>

      {/* ── Center: Market Wind ─────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          background: "#0a0e17",
          border: "1px solid #1e2d45",
          borderRadius: 10,
          padding: "8px 18px",
        }}
      >
        {/* Pulse dot */}
        <span
          className={
            isBullish === true
              ? "pulse-dot pulse-dot-green"
              : isBullish === false
              ? "pulse-dot pulse-dot-red"
              : "pulse-dot pulse-dot-gray"
          }
        />

        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start" }}>
          <div
            style={{
              fontSize: 11,
              color: "#4b5563",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              lineHeight: 1,
            }}
          >
            Market Wind · NIFTY 50
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginTop: 3,
            }}
          >
            {niftyLtp ? (
              <>
                <span
                  style={{
                    fontFamily: "JetBrains Mono, monospace",
                    fontSize: 15,
                    fontWeight: 600,
                    color: isBullish ? "var(--accent-green)" : "var(--accent-red)",
                  }}
                >
                  {niftyLtp.toFixed(2)}
                </span>
                {isBullish !== null && (
                  isBullish
                    ? <TrendingUp size={14} color="var(--accent-green)" />
                    : <TrendingDown size={14} color="var(--accent-red)" />
                )}
                {niftyVwap && (
                  <span style={{ color: "#4b5563", fontSize: 12, fontFamily: "monospace" }}>
                    VWAP {niftyVwap.toFixed(2)}
                  </span>
                )}
              </>
            ) : (
              <span style={{ color: "#4b5563", fontSize: 13 }}>
                {nifty?.error ? "API Error" : "Loading..."}
              </span>
            )}
          </div>
        </div>

        {/* Status badge */}
        {isBullish !== null && isBullish !== undefined && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              padding: "2px 8px",
              borderRadius: 9999,
              background: isBullish
                ? "rgba(0,208,132,0.15)"
                : "rgba(255,77,77,0.15)",
              color: isBullish ? "var(--accent-green)" : "var(--accent-red)",
              border: `1px solid ${isBullish ? "rgba(0,208,132,0.3)" : "rgba(255,77,77,0.3)"}`,
            }}
          >
            {isBullish ? "BULLISH" : "BEARISH"}
          </span>
        )}
      </div>

      {/* ── Right: Clock ────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        {lastRefresh && (
          <div style={{ display: "flex", alignItems: "center", gap: 5, color: "#4b5563", fontSize: 11 }}>
            <RefreshCw size={10} />
            {lastRefresh}
          </div>
        )}
        <div style={{ textAlign: "right" }}>
          <div
            style={{
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 18,
              fontWeight: 600,
              color: "#e2e8f0",
              letterSpacing: "0.04em",
              lineHeight: 1.2,
            }}
          >
            {time || "--:--:--"}
          </div>
          <div style={{ fontSize: 11, color: "#4b5563" }}>{date} IST</div>
        </div>
      </div>
    </header>
  );
}

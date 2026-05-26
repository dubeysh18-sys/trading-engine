"use client";

import { useState } from "react";
import Header from "@/components/Header";
import AlertFeed from "@/components/AlertFeed";
import BacktestHub from "@/components/BacktestHub";

export default function DashboardPage() {
  const [selectedDate, setSelectedDate] = useState<string>(
    new Date().toLocaleDateString("en-CA") // "YYYY-MM-DD" in local time
  );

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#0a0e17",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* ── Top Bar ───────────────────────────────────────── */}
      <Header selectedDate={selectedDate} setSelectedDate={setSelectedDate} />

      {/* ── Main Workspace ────────────────────────────────── */}
      <main
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 16,
          padding: "16px",
          maxWidth: "100%",
          overflowX: "hidden",
        }}
      >
        {/* Left Column: Live Alert Feed */}
        <section style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <AlertFeed />
        </section>

        {/* Right Column: EOD Backtester Hub */}
        <section style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <BacktestHub selectedDate={selectedDate} />
        </section>
      </main>

      {/* ── Footer ────────────────────────────────────────── */}
      <footer
        style={{
          borderTop: "1px solid #1e2d45",
          padding: "10px 24px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          color: "#4b5563",
          fontSize: 11,
        }}
      >
        <span>Trading Rule Engine · Upstox API V3 · 4 Golden Rules</span>
        <span style={{ display: "flex", gap: 16 }}>
          <span>📡 Chartink Webhook</span>
          <span>📊 5-min OHLCV</span>
          <span>⚡ VWAP · 9 EMA · Pivot R1</span>
        </span>
      </footer>
    </div>
  );
}

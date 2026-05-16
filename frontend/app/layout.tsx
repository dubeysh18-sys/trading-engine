import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trading Rule Engine | Intraday Scanner & Backtester",
  description:
    "Real-time intraday trading rule engine with Chartink webhooks, Upstox API, VWAP/Pivot rule evaluation, and EOD backtesting dashboard.",
  keywords: "intraday trading, scanner, VWAP, pivot points, Chartink, Upstox, backtester",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body style={{ background: "#0a0e17", minHeight: "100vh" }}>
        {children}
      </body>
    </html>
  );
}

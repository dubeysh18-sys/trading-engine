"use client";

import React, { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { WS_URL, ActiveTrade, ExitResult, Alert } from "../lib/api";

export interface NiftyStatus {
  ltp: number | null;
  vwap: number | null;
  is_bullish: boolean | null;
}

interface WebSocketContextType {
  activeTrades: Record<string, ActiveTrade>;
  exitResults: ExitResult[];
  alerts: Alert[];
  niftyStatus: NiftyStatus | null;
  isConnected: boolean;
}

const WebSocketContext = createContext<WebSocketContextType | undefined>(undefined);

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [activeTrades, setActiveTrades] = useState<Record<string, ActiveTrade>>({});
  const [exitResults, setExitResults] = useState<ExitResult[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [niftyStatus, setNiftyStatus] = useState<NiftyStatus | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    let ws: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;

    function connect() {
      console.log(`Connecting to WebSocket at ${WS_URL}...`);
      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        console.log("WebSocket connected.");
        setIsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "state") {
            setActiveTrades(data.active_trades || {});
            setExitResults(data.exit_results || []);
            if (data.alerts_history) {
              setAlerts(data.alerts_history);
            }
            if (data.nifty_status) {
              setNiftyStatus(data.nifty_status);
            }
          } else if (data.type === "alert") {
            // Append incoming alert at the top of the alerts feed
            setAlerts((prev) => [data.alert, ...prev]);
          }
        } catch (err) {
          console.error("Error parsing WebSocket message:", err);
        }
      };

      ws.onclose = () => {
        console.log("WebSocket disconnected. Reconnecting in 3 seconds...");
        setIsConnected(false);
        reconnectTimeout = setTimeout(connect, 3000);
      };

      ws.onerror = (err) => {
        console.error("WebSocket error:", err);
        ws.close();
      };
    }

    connect();

    return () => {
      if (ws) ws.close();
      clearTimeout(reconnectTimeout);
    };
  }, []);

  return (
    <WebSocketContext.Provider
      value={{
        activeTrades,
        exitResults,
        alerts,
        niftyStatus,
        isConnected,
      }}
    >
      {children}
    </WebSocketContext.Provider>
  );
}

export function useWebSocket() {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error("useWebSocket must be used within a WebSocketProvider");
  }
  return context;
}

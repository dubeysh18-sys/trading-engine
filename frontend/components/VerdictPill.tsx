"use client";

import { Alert } from "@/lib/api";

type VerdictType = Alert["verdict"];

interface Props {
  verdict: VerdictType;
  reason?: string | null;
  size?: "sm" | "md";
}

const CONFIG: Record<VerdictType, { label: string; className: string; dot: string }> = {
  ENTER: {
    label: "ENTER",
    className: "pill pill-enter",
    dot: "bg-green-400",
  },
  WAIT: {
    label: "WAIT",
    className: "pill pill-wait",
    dot: "bg-yellow-400",
  },
  SKIP: {
    label: "SKIP",
    className: "pill pill-skip",
    dot: "bg-red-400",
  },
  ERROR: {
    label: "ERR",
    className: "pill pill-error",
    dot: "bg-gray-500",
  },
  PENDING: {
    label: "...",
    className: "pill pill-pending",
    dot: "bg-gray-400",
  },
};

export default function VerdictPill({ verdict, reason, size = "md" }: Props) {
  const config = CONFIG[verdict] ?? CONFIG.PENDING;

  return (
    <span
      className={config.className}
      title={reason ?? undefined}
      style={{ fontSize: size === "sm" ? "10px" : undefined }}
    >
      {/* Coloured dot */}
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          display: "inline-block",
          flexShrink: 0,
          background:
            verdict === "ENTER"
              ? "var(--accent-green)"
              : verdict === "WAIT"
              ? "var(--accent-yellow)"
              : verdict === "SKIP"
              ? "var(--accent-red)"
              : "var(--text-muted)",
        }}
      />
      {config.label}
    </span>
  );
}

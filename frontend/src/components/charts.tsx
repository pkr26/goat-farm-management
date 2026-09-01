"use client";

/**
 * Dependency-free SVG chart kit. Color comes exclusively from the chart
 * tokens (chart-1..5) and semantic tokens, so every chart follows the
 * active theme. Charts are decorative summaries — they carry accessible
 * titles/values in text, never as the only representation of the data.
 */

import { cn } from "@/lib/utils";

export function Sparkline({
  data,
  className,
  stroke = "var(--chart-1)",
  fill = true,
  ariaLabel,
}: {
  data: number[];
  className?: string;
  stroke?: string;
  fill?: boolean;
  ariaLabel?: string;
}) {
  const width = 96;
  const height = 28;
  if (data.length < 2) {
    return (
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className={cn("h-7 w-24", className)}
        role="img"
        aria-label={ariaLabel ?? "trend"}
      >
        <line
          x1="0"
          y1={height - 1}
          x2={width}
          y2={height - 1}
          stroke="currentColor"
          strokeWidth="1.5"
          className="text-border"
        />
      </svg>
    );
  }
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const points = data.map((value, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - 2 - ((value - min) / span) * (height - 4);
    return [x, y] as const;
  });
  const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `0,${height} ${line} ${width},${height}`;
  const last = points[points.length - 1];
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cn("h-7 w-24 overflow-visible", className)}
      role="img"
      aria-label={ariaLabel ?? "trend"}
      preserveAspectRatio="none"
    >
      {fill && (
        <polygon points={area} fill={stroke} opacity="0.12" />
      )}
      <polyline
        points={line}
        fill="none"
        stroke={stroke}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={last[0]} cy={last[1]} r="2.4" fill={stroke} />
    </svg>
  );
}

export interface DonutSlice {
  label: string;
  value: number;
  color?: string;
}

/** Donut with a centered total. Slices under 0.4% are invisible-safe. */
export function Donut({
  slices,
  centerLabel,
  centerValue,
  className,
  size = 148,
  showLegend = true,
}: {
  slices: DonutSlice[];
  centerLabel?: string;
  centerValue?: string | number;
  className?: string;
  size?: number;
  /** Hide when the caller renders its own (richer) legend beside the ring. */
  showLegend?: boolean;
}) {
  const palette = [
    "var(--chart-1)",
    "var(--chart-2)",
    "var(--chart-3)",
    "var(--chart-4)",
    "var(--chart-5)",
  ];
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  // Filter once: the ring and the legend must share one array so their
  // palette indexes agree even when zero-value slices precede real ones.
  const visibleSlices = slices.filter((slice) => slice.value > 0);
  return (
    <div className={cn("flex items-center gap-5", className)}>
      <svg
        viewBox="0 0 140 140"
        width={size}
        height={size}
        className="shrink-0 -rotate-90"
        role="img"
        aria-label={
          total > 0
            ? `Distribution: ${slices
                .filter((s) => s.value > 0)
                .map((s) => `${s.label} ${s.value}`)
                .join(", ")}`
            : "No data"
        }
      >
        <circle
          cx="70"
          cy="70"
          r={radius}
          fill="none"
          strokeWidth="16"
          stroke="var(--muted)"
          opacity="0.6"
        />
        {total > 0 &&
          visibleSlices.map((slice, i) => {
            const fraction = slice.value / total;
            const dash = fraction * circumference;
            // Gap between slices scales down with the slice so tiny slices
            // keep a visible sliver instead of being eaten by the gap.
            const gap = Math.min(dash * 0.25, 2);
            const element = (
              <circle
                key={slice.label}
                cx="70"
                cy="70"
                r={radius}
                fill="none"
                strokeWidth="16"
                strokeLinecap="butt"
                stroke={slice.color ?? palette[i % palette.length]}
                strokeDasharray={`${Math.max(dash - gap, 0.75)} ${circumference - dash + gap}`}
                strokeDashoffset={-offset}
              />
            );
            offset += dash;
            return element;
          })}
      </svg>
      <div className="min-w-0 space-y-1.5 text-sm">
        {centerValue !== undefined && (
          <p className="table-numeric font-heading text-2xl font-semibold leading-none">
            {centerValue}
          </p>
        )}
        {centerLabel && (
          <p className="text-xs text-muted-foreground">{centerLabel}</p>
        )}
        {showLegend && (
        <ul className="space-y-1">
          {visibleSlices
            .map((slice, i) => (
              <li key={slice.label} className="flex items-center gap-2 text-xs">
                <span
                  aria-hidden="true"
                  className="size-2 shrink-0 rounded-full"
                  style={{ background: slice.color ?? palette[i % palette.length] }}
                />
                <span className="truncate text-muted-foreground">{slice.label}</span>
                <span className="table-numeric ml-auto font-medium">{slice.value}</span>
              </li>
            ))}
        </ul>
        )}
      </div>
    </div>
  );
}

export interface BarItem {
  label: string;
  value: number;
  /** Optional formatted display value (e.g. ₹ strings). */
  display?: string;
  tone?: "default" | "positive" | "negative";
}

/** Horizontal bar list — the honest default for category comparisons. */
export function BarList({
  items,
  className,
  ariaLabel,
}: {
  items: BarItem[];
  className?: string;
  ariaLabel?: string;
}) {
  const max = Math.max(...items.map((item) => Math.abs(item.value)), 1);
  return (
    <ul className={cn("space-y-2.5", className)} aria-label={ariaLabel} role="list">
      {items.map((item) => {
        const width = (Math.abs(item.value) / max) * 100;
        const barTone =
          item.tone === "negative"
            ? "bg-chart-3"
            : item.tone === "positive"
              ? "bg-chart-1"
              : "bg-chart-2";
        return (
          <li key={item.label} className="space-y-1">
            <div className="flex items-baseline justify-between gap-3 text-xs">
              <span className="truncate text-muted-foreground">{item.label}</span>
              <span className="table-numeric font-medium">
                {item.display ?? item.value}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
              <div
                className={cn("h-full rounded-full transition-all", barTone)}
                style={{ width: `${Math.max(width, item.value > 0 ? 2 : 0)}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

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

export interface TrendPoint {
  label: string;
  value: number;
  /** Optional formatted display value (e.g. ₹ strings) for tooltips/text. */
  display?: string;
}

/**
 * Signed vertical bar chart around a zero baseline — monthly P&L, net cash
 * flow, any series where direction is the message. Bars above the baseline
 * use chart-1, below use chart-3, so the picture survives color-blindness
 * (position encodes the sign, color only reinforces it).
 */
export function TrendChart({
  points,
  className,
  ariaLabel,
  height = 160,
}: {
  points: TrendPoint[];
  className?: string;
  ariaLabel?: string;
  height?: number;
}) {
  const width = 560;
  const pad = 4;
  const maxAbs = Math.max(...points.map((p) => Math.abs(p.value)), 1);
  const zeroY = height / 2;
  const slot = points.length > 0 ? (width - pad * 2) / points.length : width;
  const barWidth = Math.max(Math.min(slot * 0.66, 28), 3);
  return (
    <div className={cn("w-full", className)}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-40 w-full"
        role="img"
        aria-label={
          ariaLabel ??
          `Trend: ${points.map((p) => `${p.label} ${p.display ?? p.value}`).join(", ")}`
        }
        preserveAspectRatio="none"
      >
        <line
          x1={pad}
          y1={zeroY}
          x2={width - pad}
          y2={zeroY}
          stroke="var(--border)"
          strokeWidth="1"
        />
        {points.map((p, i) => {
          const magnitude = (Math.abs(p.value) / maxAbs) * (height / 2 - pad);
          const x = pad + slot * i + (slot - barWidth) / 2;
          const isNeg = p.value < 0;
          return (
            <rect
              key={p.label}
              x={x}
              y={isNeg ? zeroY : zeroY - magnitude}
              width={barWidth}
              height={Math.max(magnitude, p.value === 0 ? 0 : 1.5)}
              rx={Math.min(3, barWidth / 2)}
              fill={isNeg ? "var(--chart-3)" : "var(--chart-1)"}
              opacity={0.9}
            >
              <title>{`${p.label}: ${p.display ?? p.value}`}</title>
            </rect>
          );
        })}
      </svg>
      <div className="mt-1 flex justify-between text-[0.65rem] text-muted-foreground">
        <span>{points[0]?.label}</span>
        {points.length > 2 && (
          <span className="hidden sm:inline">
            {points[Math.floor(points.length / 2)]?.label}
          </span>
        )}
        <span>{points[points.length - 1]?.label}</span>
      </div>
    </div>
  );
}

export interface HistogramBin {
  /** Bin lower-edge label (already formatted by the caller). */
  label: string;
  value: number;
  /** Optional count unit for tooltips (e.g. "runs"). */
  unit?: string;
}

export interface HistogramMarker {
  label: string;
  value: number;
  /** Strong markers render heavier (e.g. the median). */
  strong?: boolean;
}

/**
 * Vertical histogram with optional percentile/reference markers — the
 * Monte-Carlo NPV distribution. Strong markers are solid; the rest dashed.
 * `domain` (min/max in bin-edge units) is required when markers are given.
 */
export function Histogram({
  bins,
  markers,
  domain,
  className,
  ariaLabel,
  height = 180,
}: {
  bins: HistogramBin[];
  /** Reference lines drawn in bin-edge units. */
  markers?: HistogramMarker[];
  domain?: [number, number];
  className?: string;
  ariaLabel?: string;
  height?: number;
}) {
  const width = 560;
  const pad = 6;
  const maxCount = Math.max(...bins.map((b) => b.value), 1);
  const slot = bins.length > 0 ? (width - pad * 2) / bins.length : width;
  const barWidth = Math.max(slot * 0.8, 2);
  const markerX = (value: number) => {
    if (!domain || domain[1] <= domain[0]) return null;
    const [lo, hi] = domain;
    return pad + ((value - lo) / (hi - lo)) * (width - pad * 2);
  };
  const markerLines = (markers ?? []).flatMap((marker) => {
    const x = markerX(marker.value);
    if (x === null) return [];
    return [
      <line
        key={`${marker.label}-${marker.value}`}
        x1={x}
        y1={2}
        x2={x}
        y2={height - 2}
        stroke="var(--foreground)"
        strokeOpacity={marker.strong ? 0.75 : 0.45}
        strokeWidth={marker.strong ? 1.5 : 1}
        strokeDasharray={marker.strong ? undefined : "4 3"}
      >
        <title>{`${marker.label}: ${marker.value}`}</title>
      </line>,
    ];
  });
  return (
    <div className={cn("w-full", className)}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-44 w-full"
        role="img"
        aria-label={
          ariaLabel ??
          `Distribution: ${bins.map((b) => `${b.label}: ${b.value}`).join(", ")}`
        }
        preserveAspectRatio="none"
      >
        {bins.map((b, i) => {
          const h = (b.value / maxCount) * (height - pad);
          const x = pad + slot * i + (slot - barWidth) / 2;
          return (
            <rect
              key={b.label}
              x={x}
              y={height - pad - h}
              width={barWidth}
              height={Math.max(h, b.value > 0 ? 1 : 0)}
              fill="var(--chart-1)"
              opacity={0.85}
              rx={Math.min(2, barWidth / 3)}
            >
              <title>{`${b.label}: ${b.value}${b.unit ? ` ${b.unit}` : ""}`}</title>
            </rect>
          );
        })}
        {markerLines}
      </svg>
    </div>
  );
}

export interface CurvePoint {
  /** x value (e.g. days in milk) and y value (e.g. litres). */
  x: number;
  y: number;
  /** Optional x-axis label override for sparse tick labels. */
  xLabel?: string;
}

/** Smooth line chart with a soft area fill — lactation curves, yields over
 * time. Points beyond `xTicks` get sparse first/middle/last labels. */
export function LineChart({
  points,
  className,
  ariaLabel,
  yLabel,
  height = 180,
}: {
  points: CurvePoint[];
  className?: string;
  ariaLabel?: string;
  yLabel?: string;
  height?: number;
}) {
  const width = 560;
  const padY = 10;
  const padX = 8;
  if (points.length < 2) {
    return (
      <div
        className={cn(
          "flex h-28 items-center justify-center rounded-lg border border-dashed text-sm text-muted-foreground",
          className,
        )}
      >
        Not enough data to plot yet.
      </div>
    );
  }
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const px = (x: number) => padX + ((x - minX) / spanX) * (width - padX * 2);
  const py = (y: number) => height - padY - ((y - minY) / spanY) * (height - padY * 2);
  const line = points.map((p) => `${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`).join(" ");
  const area = `${padX},${height - padY} ${line} ${width - padX},${height - padY}`;
  const last = points[points.length - 1];
  const ticks = [points[0], points[Math.floor(points.length / 2)], last];
  return (
    <div className={cn("w-full", className)}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-44 w-full"
        role="img"
        aria-label={
          ariaLabel ?? `${yLabel ?? "series"} from ${ticks.map((p) => `${p.xLabel ?? p.x}: ${p.y}`).join(" to ")}`
        }
        preserveAspectRatio="none"
      >
        <polygon points={area} fill="var(--chart-1)" opacity={0.1} />
        <polyline
          points={line}
          fill="none"
          stroke="var(--chart-1)"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx={px(last.x)} cy={py(last.y)} r="3" fill="var(--chart-1)" />
      </svg>
      <div className="mt-1 flex justify-between text-[0.65rem] text-muted-foreground">
        {ticks.map((p, i) => (
          <span key={`${p.x}-${i}`}>{p.xLabel ?? p.x}</span>
        ))}
      </div>
    </div>
  );
}

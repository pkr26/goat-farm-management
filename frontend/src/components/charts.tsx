"use client";

/**
 * Dependency-free SVG chart kit. Color comes exclusively from the chart
 * tokens (chart-1..5) and semantic tokens, so every chart follows the
 * active theme. Charts are decorative summaries — they carry accessible
 * titles/values in text, never as the only representation of the data.
 */

import { cn } from "@/lib/utils";

export interface DonutSlice {
  label: string;
  value: number;
  // FE-6 (2026-09-16): no caller-supplied `color` — a raw string here flowed
  // straight into `style={{background}}`/`stroke` under a CSP that allows
  // inline styles. Palette assignment only.
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
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  // Non-finite values (a derived slice with bad math — the JSON API can
  // never carry them) are dropped before the total: a single ±Infinity
  // would otherwise poison it and render NaN dash arrays. Negatives stay
  // in the total so exact cancellation still reads as "No data".
  const finiteSlices = slices.filter((slice) => Number.isFinite(slice.value));
  const total = finiteSlices.reduce((sum, slice) => sum + slice.value, 0);
  // Filter once: the ring and the legend must share one array so their
  // palette indexes agree even when zero-value slices precede real ones.
  const visibleSlices = finiteSlices.filter((slice) => slice.value > 0);
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
            ? `Distribution: ${visibleSlices
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
                stroke={palette[i % palette.length]}
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
                  style={{ background: palette[i % palette.length] }}
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
  /** Formatted display value (e.g. ₹ strings) for tooltips. */
  display?: string;
  /** Strong markers render heavier (e.g. the median). */
  strong?: boolean;
}

/**
 * Vertical histogram with optional percentile/reference markers — the
 * Monte-Carlo NPV distribution. Strong markers are solid; the rest dashed.
 * `domain` (min/max in bin-edge units) is required when markers are given;
 * marker positions are clamped to the plot so out-of-domain values can never
 * disappear past the edge.
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
  const finiteBins = bins.filter((b) => Number.isFinite(b.value));
  const maxCount = Math.max(...finiteBins.map((b) => b.value), 1);
  const slot = finiteBins.length > 0 ? (width - pad * 2) / finiteBins.length : width;
  const barWidth = Math.max(slot * 0.8, 2);
  const markerX = (value: number) => {
    if (!domain || domain[1] <= domain[0] || !Number.isFinite(value)) return null;
    const [lo, hi] = domain;
    const raw = pad + ((value - lo) / (hi - lo)) * (width - pad * 2);
    return Math.min(Math.max(raw, pad), width - pad);
  };
  const markerLines = (markers ?? []).flatMap((marker, i) => {
    const x = markerX(marker.value);
    if (x === null) return [];
    return [
      <line
        key={`marker-${i}-${marker.label}`}
        x1={x}
        y1={pad}
        x2={x}
        y2={height - pad}
        stroke="var(--foreground)"
        strokeOpacity={marker.strong ? 0.75 : 0.45}
        strokeWidth={marker.strong ? 1.5 : 1}
        strokeDasharray={marker.strong ? undefined : "4 3"}
      >
        <title>{`${marker.label}: ${marker.display ?? marker.value}`}</title>
      </line>,
    ];
  });
  return (
    <div className={cn("w-full", className)}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ height }}
        role="img"
        aria-label={
          ariaLabel ??
          `Distribution: ${finiteBins.map((b) => `${b.label}: ${b.value}`).join(", ")}`
        }
        preserveAspectRatio="none"
      >
        {finiteBins.map((b, i) => {
          const h = (b.value / maxCount) * (height - pad);
          const x = pad + slot * i + (slot - barWidth) / 2;
          return (
            <rect
              key={`bin-${i}-${b.label}`}
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

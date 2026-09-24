/**
 * Geometry-level regression tests for the dependency-free chart kit.
 *
 * These complement charts.test.tsx by asserting *concrete* SVG geometry
 * (exact point strings, bar/rect coordinates, arc dash math, tick labels,
 * stroke colours and dash behaviour) so that silent math drift — a mutated
 * `+`/`-`, a swapped min/max, an off-by-one pad — cannot survive.
 */
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Donut, Histogram } from "@/components/charts";

const attrNum = (el: Element | null, name: string) => Number(el?.getAttribute(name));
const dashParts = (el: Element) =>
  (el.getAttribute("stroke-dasharray") ?? "").split(/\s+/).map(Number);
const donutSegments = (container: HTMLElement) =>
  Array.from(container.querySelectorAll("circle[stroke-dasharray]"));

describe("Donut geometry", () => {
  const C = 2 * Math.PI * 54;

  it("converts slice fractions into dash lengths on the 54-radius ring", () => {
    const { container } = render(
      <Donut
        slices={[
          { label: "Weaners", value: 12 },
          { label: "Breeders", value: 6 },
        ]}
      />,
    );
    const segments = donutSegments(container);
    expect(segments).toHaveLength(2);

    const dashA = (12 / 18) * C;
    const dashB = (6 / 18) * C;
    // First slice: visible dash minus the 2px gap, remainder of the ring after it.
    expect(dashParts(segments[0])[0]).toBeCloseTo(dashA - 2, 6);
    expect(dashParts(segments[0])[1]).toBeCloseTo(C - dashA + 2, 6);
    // Second slice starts where the first ended (negative offset accumulates).
    expect(dashParts(segments[1])[0]).toBeCloseTo(dashB - 2, 6);
    expect(dashParts(segments[1])[1]).toBeCloseTo(C - dashB + 2, 6);
    expect(attrNum(segments[1], "stroke-dashoffset")).toBeCloseTo(-dashA, 6);
    expect(attrNum(segments[0], "stroke-dashoffset")).toBeCloseTo(0, 6);
  });

  it("shrinks the gap for small slices and never drops below a 0.75 sliver", () => {
    const { container } = render(
      <Donut
        slices={[
          { label: "Big", value: 99 },
          { label: "Sliver", value: 1 },
        ]}
      />,
    );
    const segments = donutSegments(container);
    const dashSmall = (1 / 100) * C;
    const gapSmall = Math.min(dashSmall * 0.25, 2);
    expect(dashParts(segments[1])[0]).toBeCloseTo(dashSmall - gapSmall, 6);
    expect(dashParts(segments[1])[1]).toBeCloseTo(C - dashSmall + gapSmall, 6);
    // Large slice gap caps at 2px.
    expect(dashParts(segments[0])[0]).toBeCloseTo((99 / 100) * C - 2, 6);

    const { container: tiny } = render(
      <Donut
        slices={[
          { label: "Big", value: 9999 },
          { label: "Tiny", value: 1 },
        ]}
      />,
    );
    // A truly tiny slice is clamped up to the 0.75 minimum visible dash.
    expect(dashParts(donutSegments(tiny)[1])[0]).toBeCloseTo(0.75, 6);
  });

  it("assigns palette colours in order and wraps around after five slices", () => {
    const { container } = render(
      <Donut
        slices={[1, 2, 3, 4, 5, 6].map((value, i) => ({ label: `s${i}`, value }))}
      />,
    );
    const strokes = donutSegments(container).map((s) => s.getAttribute("stroke"));
    expect(strokes).toEqual([
      "var(--chart-1)",
      "var(--chart-2)",
      "var(--chart-3)",
      "var(--chart-4)",
      "var(--chart-5)",
      "var(--chart-1)",
    ]);
  });

  it("ignores any legacy per-slice colour: palette assignment only (FE-6)", () => {
    // The `color` field was removed from DonutSlice (2026-09-16) — it flowed
    // a raw caller string into inline style/stroke under 'unsafe-inline' CSP.
    const { container } = render(
      <Donut
        slices={[
          { label: "First", value: 2 },
          { label: "Second", value: 3 },
        ]}
      />,
    );
    const segments = donutSegments(container);
    expect(segments[0].getAttribute("stroke")).toBe("var(--chart-1)");
    expect(segments[1].getAttribute("stroke")).toBe("var(--chart-2)");
  });

  it("excludes zero-value slices from the accessible distribution label", () => {
    const { container } = render(
      <Donut
        slices={[
          { label: "Empty", value: 0 },
          { label: "Only", value: 9 },
        ]}
      />,
    );
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toBe(
      "Distribution: Only 9",
    );
  });

  it("renders no segments and 'No data' when positives cancel negatives", () => {
    const { container } = render(
      <Donut
        slices={[
          { label: "Plus", value: 5 },
          { label: "Minus", value: -5 },
        ]}
      />,
    );
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toBe("No data");
    expect(donutSegments(container)).toHaveLength(0);
  });

  it("renders center value and label paragraphs only when provided", () => {
    const { container } = render(
      <Donut slices={[{ label: "A", value: 1 }]} centerValue={18} centerLabel="active animals" />,
    );
    const texts = Array.from(container.querySelectorAll("p")).map((p) => p.textContent);
    expect(texts).toEqual(["18", "active animals"]);

    const bare = render(<Donut slices={[{ label: "A", value: 1 }]} />).container;
    expect(bare.querySelectorAll("p")).toHaveLength(0);
  });

  it("lays the ring and legend out side by side", () => {
    const { container } = render(<Donut slices={[{ label: "A", value: 1 }]} />);
    expect(container.firstElementChild?.className).toContain("flex items-center gap-5");
  });
});

describe("Histogram geometry", () => {
  it("distributes bins evenly across the padded 560-wide plot", () => {
    const { container } = render(
      <Histogram
        bins={[
          { label: "low", value: 1, unit: "runs" },
          { label: "high", value: 2 },
        ]}
        height={180}
      />,
    );
    const rects = container.querySelectorAll("rect");
    expect(rects).toHaveLength(2);

    const slot = (560 - 12) / 2;
    const barWidth = Math.max(slot * 0.8, 2);
    const leftInset = (slot - barWidth) / 2;
    // Half-height first bar: y + height meet at the baseline (180 - pad).
    expect(attrNum(rects[0], "x")).toBeCloseTo(6 + leftInset, 6);
    expect(attrNum(rects[0], "y")).toBeCloseTo(87, 6);
    expect(attrNum(rects[0], "width")).toBeCloseTo(barWidth, 6);
    expect(attrNum(rects[0], "height")).toBeCloseTo(87, 6);
    expect(rects[0].getAttribute("rx")).toBe("2");
    // Max bin touches the top of the plot area.
    expect(attrNum(rects[1], "x")).toBeCloseTo(6 + slot + leftInset, 6);
    expect(attrNum(rects[1], "y")).toBeCloseTo(0, 6);
    expect(attrNum(rects[1], "height")).toBeCloseTo(174, 6);

    expect(container.querySelector("rect title")?.textContent).toBe("low: 1 runs");
    // Bins without a unit get a bare "label: value" tooltip (no stray suffix).
    expect(container.querySelectorAll("rect title")[1].textContent).toBe("high: 2");
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toBe(
      "Distribution: low: 1, high: 2",
    );
  });

  it("renders the viewBox and pixel height it was configured with", () => {
    const { container } = render(
      <Histogram bins={[{ label: "a", value: 1 }]} height={200} />,
    );
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("viewBox")).toBe("0 0 560 200");
    expect(svg?.getAttribute("style")).toContain("height: 200px");
    expect(container.firstElementChild?.className).toContain("w-full");
  });

  it("gives zero bins zero height but keeps positive slivers at 1px", () => {
    const { container } = render(
      <Histogram
        bins={[
          { label: "zero", value: 0 },
          { label: "tiny", value: 0.0001 },
          { label: "max", value: 10 },
        ]}
      />,
    );
    const heights = Array.from(container.querySelectorAll("rect")).map((r) =>
      r.getAttribute("height"),
    );
    expect(heights[0]).toBe("0");
    expect(heights[1]).toBe("1");
    expect(heights[2]).toBe("174");
  });

  it("rounds narrow bars instead of always clamping rx to 2", () => {
    const bins = Array.from({ length: 100 }, (_, i) => ({ label: `b${i}`, value: i + 1 }));
    const { container } = render(<Histogram bins={bins} />);
    const barWidth = ((560 - 12) / 100) * 0.8;
    expect(attrNum(container.querySelector("rect"), "rx")).toBeCloseTo(barWidth / 3, 3);
  });

  it("positions marker lines linearly across the domain, spanning the plot", () => {
    const { container } = render(
      <Histogram
        bins={[
          { label: "a", value: 1 },
          { label: "b", value: 2 },
        ]}
        domain={[0, 2]}
        markers={[
          { label: "lo", value: 0 },
          { label: "mid", value: 1 },
          { label: "hi", value: 2 },
        ]}
      />,
    );
    const lines = container.querySelectorAll("svg > line");
    expect(lines).toHaveLength(3);
    const xs = Array.from(lines).map((l) => attrNum(l, "x1"));
    expect(xs[0]).toBeCloseTo(6, 6);
    expect(xs[1]).toBeCloseTo(280, 6);
    expect(xs[2]).toBeCloseTo(554, 6);
    for (const line of lines) {
      expect(line.getAttribute("y1")).toBe("6");
      expect(line.getAttribute("y2")).toBe("174");
      expect(line.getAttribute("x1")).toBe(line.getAttribute("x2"));
    }
  });

  it("maps markers correctly when the domain does not start at zero", () => {
    const { container } = render(
      <Histogram
        bins={[{ label: "a", value: 1 }]}
        domain={[10, 12]}
        markers={[
          { label: "lo", value: 10 },
          { label: "mid", value: 11 },
          { label: "hi", value: 12 },
        ]}
      />,
    );
    const xs = Array.from(container.querySelectorAll("svg > line")).map((l) =>
      attrNum(l, "x1"),
    );
    expect(xs[0]).toBeCloseTo(6, 6);
    expect(xs[1]).toBeCloseTo(280, 6);
    expect(xs[2]).toBeCloseTo(554, 6);
  });

  it("leaves no stray text behind when markers are dropped", () => {
    const noDomain = render(
      <Histogram bins={[{ label: "a", value: 1 }]} markers={[{ label: "m", value: 1 }]} />,
    );
    // Only the bin tooltip text may appear inside the svg.
    expect(noDomain.container.querySelector("svg")?.textContent).toBe("a: 1");

    const nonFinite = render(
      <Histogram
        bins={[{ label: "a", value: 1 }]}
        domain={[0, 2]}
        markers={[{ label: "bad", value: Number.NaN }]}
      />,
    );
    expect(nonFinite.container.querySelector("svg")?.textContent).toBe("a: 1");
  });

  it("clamps out-of-domain markers onto the plot edges", () => {
    const { container } = render(
      <Histogram
        bins={[{ label: "a", value: 1 }]}
        domain={[0, 2]}
        markers={[
          { label: "over", value: 99 },
          { label: "under", value: -50 },
        ]}
      />,
    );
    const xs = Array.from(container.querySelectorAll("svg > line")).map((l) =>
      attrNum(l, "x1"),
    );
    expect(xs[0]).toBeCloseTo(554, 6);
    expect(xs[1]).toBeCloseTo(6, 6);
  });

  it("draws weak markers dashed and strong markers solid", () => {
    const { container } = render(
      <Histogram
        bins={[{ label: "a", value: 1 }]}
        domain={[0, 2]}
        markers={[
          { label: "p5", value: 0.5 },
          { label: "median", value: 1, strong: true },
        ]}
      />,
    );
    const lines = container.querySelectorAll("svg > line");
    expect(lines[0].getAttribute("stroke-dasharray")).toBe("4 3");
    expect(lines[1].getAttribute("stroke-dasharray")).toBeNull();
    expect(lines[1].getAttribute("stroke-width")).toBe("1.5");
    expect(lines[1].getAttribute("stroke-opacity")).toBe("0.75");
  });

  it("drops markers when the domain is missing, degenerate or inverted", () => {
    const bins = [{ label: "a", value: 1 }];
    const noDomain = render(<Histogram bins={bins} markers={[{ label: "m", value: 1 }]} />);
    expect(noDomain.container.querySelectorAll("svg > line")).toHaveLength(0);

    const degenerate = render(
      <Histogram bins={bins} domain={[5, 5]} markers={[{ label: "m", value: 5 }]} />,
    );
    expect(degenerate.container.querySelectorAll("svg > line")).toHaveLength(0);

    const inverted = render(
      <Histogram bins={bins} domain={[10, 0]} markers={[{ label: "m", value: 5 }]} />,
    );
    expect(inverted.container.querySelectorAll("svg > line")).toHaveLength(0);
  });

  it("drops markers with non-finite values", () => {
    const { container } = render(
      <Histogram
        bins={[{ label: "a", value: 1 }]}
        domain={[0, 2]}
        markers={[{ label: "bad", value: Number.NaN }]}
      />,
    );
    expect(container.querySelectorAll("svg > line")).toHaveLength(0);
  });
});

/** Mutation-hardening round 2 (2026-09-23 campaign): pins the boundary
 *  constants that survived round 1 — the total>0 gate at exactly 1, the
 *  maxCount floor of 1 (both against small and all-zero data), the
 *  single-bin slot (vs the zero-bin fallback), the 2px bar-width floor,
 *  marker stroke styling, and bar opacity. */
describe("Histogram boundary constants", () => {
  it("scales a count-1 bin against the maxCount floor of exactly 1", () => {
    const { container } = render(
      <Histogram bins={[{ label: "a", value: 1 }]} height={200} />,
    );
    const rect = container.querySelector("rect")!;
    expect(rect).not.toBeNull();
    // slot = (560 - 12) / 1 = 548; barWidth = 548 * 0.8 = 438.4;
    // h = (1 / max(1, 1)) * (200 - 6) = 194; y = 200 - 6 - 194 = 0.
    expect(attrNum(rect, "height")).toBeCloseTo(194, 6);
    expect(attrNum(rect, "y")).toBeCloseTo(0, 6);
    expect(attrNum(rect, "width")).toBeCloseTo(438.4, 6);
    expect(attrNum(rect, "x")).toBeCloseTo(6 + (548 - 438.4) / 2, 6);
  });

  it("renders all-zero bins as zero-height bars, never NaN", () => {
    const { container } = render(
      <Histogram bins={[{ label: "a", value: 0 }, { label: "b", value: 0 }]} height={100} />,
    );
    const rects = Array.from(container.querySelectorAll("rect"));
    expect(rects).toHaveLength(2);
    for (const rect of rects) {
      expect(Number(rect.getAttribute("height"))).toBe(0);
      expect(Number.isNaN(Number(rect.getAttribute("y")))).toBe(false);
    }
  });

  it("floors the bar width at 2 px when bins crowd the axis", () => {
    const { container } = render(
      <Histogram
        bins={Array.from({ length: 400 }, (_, i) => ({ label: `b${i}`, value: 1 }))}
        height={100}
      />,
    );
    // slot = 548 / 400 = 1.37 → 1.37 * 0.8 < 2 → clamped to 2.
    const rect = container.querySelector("rect")!;
    expect(attrNum(rect, "width")).toBeCloseTo(2, 6);
  });

  it("styles weak and strong markers exactly", () => {
    const { container } = render(
      <Histogram
        bins={[{ label: "a", value: 3 }]}
        domain={[0, 10]}
        markers={[
          { label: "avg", value: 5 },
          { label: "target", value: 8, strong: true },
        ]}
        height={100}
      />,
    );
    const lines = Array.from(container.querySelectorAll("line"));
    expect(lines).toHaveLength(2);
    const [weak, strong] = lines;
    expect(attrNum(weak, "stroke-opacity")).toBeCloseTo(0.45, 6);
    expect(attrNum(weak, "stroke-width")).toBeCloseTo(1, 6);
    expect(weak.getAttribute("stroke-dasharray")).toBe("4 3");
    expect(attrNum(strong, "stroke-opacity")).toBeCloseTo(0.75, 6);
    expect(attrNum(strong, "stroke-width")).toBeCloseTo(1.5, 6);
    expect(strong.getAttribute("stroke-dasharray")).toBeNull();
    // Marker titles prefer the display spelling and fall back to the value.
    expect(weak.querySelector("title")?.textContent).toBe("avg: 5");
    expect(strong.querySelector("title")?.textContent).toBe("target: 8");
  });

  it("bars render at opacity 0.85", () => {
    const { container } = render(<Histogram bins={[{ label: "a", value: 2 }]} />);
    expect(attrNum(container.querySelector("rect"), "opacity")).toBeCloseTo(0.85, 6);
  });

  it("an empty histogram keeps the zero-bin fallback aria and no bars", () => {
    const { container } = render(<Histogram bins={[]} aria-label="" />);
    const svg = container.querySelector("svg")!;
    // ariaLabel "" must NOT shadow the computed fallback… the fallback is the
    // empty-distribution summary.
    expect(svg.getAttribute("aria-label")).toBe("Distribution: ");
    expect(container.querySelector("rect")).toBeNull();
  });
});

describe("Donut boundary constants", () => {
  it("renders a single count-1 slice (total exactly 1)", () => {
    const { container } = render(
      <Donut slices={[{ label: "Only", value: 1 }]} />,
    );
    const segments = donutSegments(container);
    expect(segments).toHaveLength(1);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("aria-label")).toBe("Distribution: Only 1");
  });

  it("an all-zero donut says No data and renders no slices", () => {
    const { container } = render(
      <Donut slices={[{ label: "A", value: 0 }, { label: "B", value: 0 }]} />,
    );
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("aria-label")).toBe("No data");
    // Only the muted background ring, no palette segments.
    expect(donutSegments(container)).toHaveLength(0);
  });
});

describe("Donut default size", () => {
  it("defaults the svg to exactly 148 px", () => {
    const { container } = render(<Donut slices={[{ label: "A", value: 1 }]} />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("width")).toBe("148");
    expect(svg.getAttribute("height")).toBe("148");
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BarList, Donut, Histogram, LineChart, Sparkline } from "@/components/charts";

describe("Donut", () => {
  it("summarizes every visible slice in its accessible label and legend", () => {
    render(
      <Donut
        slices={[
          { label: "Weaners", value: 12 },
          { label: "Breeders", value: 6 },
        ]}
        centerValue={18}
        centerLabel="active animals"
      />,
    );
    const img = screen.getByRole("img");
    expect(img.getAttribute("aria-label")).toBe("Distribution: Weaners 12, Breeders 6");
    expect(screen.getByText("Weaners")).toBeInTheDocument();
    expect(screen.getByText("Breeders")).toBeInTheDocument();
    expect(screen.getByText("18")).toBeInTheDocument();
  });

  it("drops zero slices from the legend without shifting legend colors", () => {
    const { container } = render(
      <Donut
        slices={[
          { label: "Empty", value: 0 },
          { label: "Only", value: 9 },
        ]}
      />,
    );
    expect(screen.queryByText("Empty")).not.toBeInTheDocument();
    expect(screen.getByText("Only")).toBeInTheDocument();
    // One visible slice → exactly one colored ring segment besides the track.
    const segments = Array.from(container.querySelectorAll("circle[stroke-dasharray]"));
    expect(segments).toHaveLength(1);
  });

  it("hides the legend on request while keeping the ring and center value", () => {
    render(
      <Donut slices={[{ label: "Hidden", value: 3 }]} showLegend={false} centerValue={3} />,
    );
    expect(screen.queryByText("Hidden")).not.toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("renders an empty ring and no data label when everything is zero", () => {
    render(<Donut slices={[{ label: "None", value: 0 }]} />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe("No data");
  });

  it("ignores non-finite slices instead of rendering NaN dash arrays", () => {
    // RT-Q-3: ±Infinity (or NaN) in one slice must not poison the total —
    // Infinity/Infinity yields NaN fractions and an invalid strokeDasharray.
    // Unreachable via the JSON API; guards future derived-math callers.
    const { container } = render(
      <Donut
        slices={[
          { label: "Broken", value: Number.POSITIVE_INFINITY },
          { label: "Also broken", value: Number.NEGATIVE_INFINITY },
          { label: "Real", value: 4 },
        ]}
      />,
    );
    const segments = Array.from(container.querySelectorAll("circle[stroke-dasharray]"));
    expect(segments).toHaveLength(1);
    expect(segments[0].getAttribute("stroke-dasharray")).not.toContain("NaN");
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe(
      "Distribution: Real 4",
    );
    expect(screen.queryByText("Broken")).not.toBeInTheDocument();
    expect(screen.getByText("Real")).toBeInTheDocument();
  });
});

describe("Sparkline", () => {
  it("renders a flat fallback for fewer than two points", () => {
    const { container } = render(<Sparkline data={[4]} ariaLabel="weights" />);
    expect(container.querySelector("polyline")).toBeNull();
    expect(container.querySelector("line")).not.toBeNull();
  });

  it("draws a line and end dot for a real series, including zeros", () => {
    const { container } = render(<Sparkline data={[0, 0, 5, 0]} ariaLabel="weights" />);
    expect(container.querySelector("polyline")).not.toBeNull();
    expect(container.querySelector("circle")).not.toBeNull();
  });

  it("handles an all-equal series without dividing by zero", () => {
    const { container } = render(<Sparkline data={[3, 3, 3]} ariaLabel="steady" />);
    expect(container.querySelector("polyline")).not.toBeNull();
  });
});

describe("BarList", () => {
  it("renders labels with formatted and raw values", () => {
    render(
      <BarList
        items={[
          { label: "Fodder", value: 1200, display: "₹1,200" },
          { label: "Feed", value: 300 },
        ]}
      />,
    );
    expect(screen.getByText("Fodder")).toBeInTheDocument();
    expect(screen.getByText("₹1,200")).toBeInTheDocument();
    expect(screen.getByText("300")).toBeInTheDocument();
  });

  it("keeps bars measurable when every value is zero", () => {
    const { container } = render(
      <BarList items={[{ label: "Empty", value: 0 }]} />,
    );
    const bars = container.querySelectorAll("div.h-1\\.5 > div");
    expect(bars).toHaveLength(1);
    expect(bars[0].getAttribute("style")).not.toContain("width: NaN");
  });
});

describe("Histogram", () => {
  const bins = Array.from({ length: 20 }, (_, i) => ({
    label: `bin-${i}`,
    value: i,
    unit: "runs",
  }));

  it("renders a marker line inside the plot for in-domain percentiles", () => {
    const { container } = render(
      <Histogram
        bins={bins}
        domain={[0, 19]}
        markers={[
          { label: "P5", value: 0.95 },
          { label: "Median (P50)", value: 9.5, strong: true },
          { label: "P95", value: 18.05 },
        ]}
      />,
    );
    const lines = container.querySelectorAll("line[stroke-dasharray], line:not([stroke-dasharray])");
    // Three marker lines exist, none left the viewBox.
    const markers = Array.from(container.querySelectorAll("svg > line"));
    expect(markers).toHaveLength(3);
    for (const marker of markers) {
      expect(Number(marker.getAttribute("x1"))).toBeGreaterThanOrEqual(6);
      expect(Number(marker.getAttribute("x1"))).toBeLessThanOrEqual(554);
    }
    void lines;
  });

  it("clamps out-of-domain markers into the plot instead of losing them", () => {
    const { container } = render(
      <Histogram bins={bins} domain={[0, 19]} markers={[{ label: "Over", value: 40 }]} />,
    );
    const markers = container.querySelectorAll("svg > line");
    expect(markers).toHaveLength(1);
    expect(Number(markers[0].getAttribute("x1"))).toBeLessThanOrEqual(554);
  });

  it("uses the marker's formatted display value in its tooltip", () => {
    const { container } = render(
      <Histogram
        bins={bins}
        domain={[0, 19]}
        markers={[{ label: "P50", value: 9.5, display: "₹9.5L", strong: true }]}
      />,
    );
    expect(container.querySelector("svg > line title")?.textContent).toBe("P50: ₹9.5L");
  });

  it("ignores non-finite bin values instead of collapsing the chart", () => {
    const { container } = render(
      <Histogram bins={[{ label: "bad", value: Number.NaN }, ...bins]} domain={[0, 19]} />,
    );
    expect(container.querySelectorAll("rect")).toHaveLength(20);
  });
});

describe("LineChart", () => {
  const points = [
    { x: 0, y: 1, xLabel: "Aug 3" },
    { x: 1, y: 2, xLabel: "Aug 4" },
    { x: 2, y: 0.5, xLabel: "Aug 5" },
  ];

  it("renders one tick label per sparse tick point in series order", () => {
    const { container } = render(<LineChart points={points} yLabel="litres" />);
    const ticks = Array.from(container.querySelectorAll(".mt-1 span")).map(
      (node) => node.textContent,
    );
    expect(ticks).toEqual(["Aug 3", "Aug 4", "Aug 5"]);
  });

  it("collapses duplicate ticks on a two-point series", () => {
    const { container } = render(
      <LineChart
        points={[
          { x: 0, y: 1, xLabel: "Aug 3" },
          { x: 1, y: 3, xLabel: "Aug 4" },
        ]}
      />,
    );
    const ticks = Array.from(container.querySelectorAll(".mt-1 span")).map(
      (node) => node.textContent,
    );
    expect(ticks).toEqual(["Aug 3", "Aug 4"]);
  });

  it("shows the y-axis unit visually and attaches per-point tooltips", () => {
    const { container } = render(<LineChart points={points} yLabel="litres" />);
    expect(container.querySelector("p")?.textContent).toBe("litres");
    const titles = Array.from(container.querySelectorAll("circle title")).map(
      (node) => node.textContent,
    );
    expect(titles).toEqual(["Aug 3: 1 litres", "Aug 4: 2 litres", "Aug 5: 0.5 litres"]);
  });

  it("renders its stated height on the svg", () => {
    const { container } = render(<LineChart points={points} height={120} />);
    expect(container.querySelector("svg")?.getAttribute("style")).toContain("height: 120px");
  });

  it("falls back to the empty state with fewer than two finite points", () => {
    render(<LineChart points={[{ x: 0, y: 1 }, { x: 1, y: Number.NaN }]} />);
    expect(screen.getByText("Not enough data to plot yet.")).toBeInTheDocument();
  });
});

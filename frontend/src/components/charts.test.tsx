import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Donut, Histogram } from "@/components/charts";

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

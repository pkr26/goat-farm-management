import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BarList, Donut, Sparkline } from "@/components/charts";

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
          { label: "Milk", value: 1200, display: "₹1,200" },
          { label: "Feed", value: 300 },
        ]}
      />,
    );
    expect(screen.getByText("Milk")).toBeInTheDocument();
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

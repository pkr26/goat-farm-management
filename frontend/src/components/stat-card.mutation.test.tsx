/**
 * Mutation-hardening for src/components/stat-card.tsx: the default tint of
 * the icon plate, the exact conditions under which the trend/hint row is
 * rendered at all, and the layout classes of the trend pill.
 */

import { render, screen } from "@testing-library/react";
import { Activity } from "lucide-react";
import { describe, expect, it } from "vitest";

import { StatCard } from "@/components/stat-card";

describe("StatCard", () => {
  it("paints the neutral tint on the icon plate by default", () => {
    const { container } = render(
      <StatCard label="Total animals" value={7} icon={Activity} />,
    );

    const plate = container.querySelector("span.bg-muted");
    expect(plate).not.toBeNull();
    expect(plate).toHaveClass(
      "flex",
      "size-10",
      "items-center",
      "justify-center",
      "rounded-xl",
      "bg-muted",
      "text-muted-foreground",
    );
    expect(plate?.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("renders no trend/hint row when neither is supplied", () => {
    const { container } = render(
      <StatCard label="Total animals" value={7} icon={Activity} />,
    );

    // The label and value paragraphs are the only children of the text column.
    const value = screen.getByText("7");
    expect(value.tagName).toBe("P");
    expect(value.nextElementSibling).toBeNull();
    expect(container.querySelector("p.flex")).toBeNull();
    expect(container.querySelector(".lucide-trending-up")).toBeNull();
    expect(container.querySelector(".lucide-trending-down")).toBeNull();
  });

  it("styles the trend pill with its own weight and inline layout", () => {
    render(
      <StatCard
        label="Milk today"
        value={12}
        icon={Activity}
        trend={{ value: "+2", direction: "up", tone: "positive" }}
      />,
    );

    const pill = screen.getByText("+2").closest("span");
    expect(pill).not.toBeNull();
    expect(pill).toHaveClass(
      "inline-flex",
      "items-center",
      "gap-1",
      "font-medium",
      "text-success",
    );
    expect(pill?.querySelector(".lucide-trending-up")).toBeInTheDocument();
  });

  it("keeps the hint row for a numeric-zero hint without inventing a trend", () => {
    const { container } = render(
      <StatCard label="Births" value={0} icon={Activity} hint={0} />,
    );

    const hintRow = container.querySelector("p.flex");
    expect(hintRow).not.toBeNull();
    expect(hintRow).toHaveTextContent("0");
    expect(hintRow).toHaveClass("text-xs", "text-muted-foreground");
    expect(container.querySelector(".lucide-trending-up")).toBeNull();
    expect(container.querySelector(".lucide-trending-down")).toBeNull();
  });
});

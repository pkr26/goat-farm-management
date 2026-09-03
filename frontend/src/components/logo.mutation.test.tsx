/**
 * Mutation-hardening for src/components/logo.tsx: the mark and wordmark sit
 * in a flex row with the brand spacing, and the wordmark carries the heading
 * type treatment.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Logo } from "@/components/logo";

describe("Logo", () => {
  it("lays the mark and wordmark out in a spaced flex row", () => {
    const { container } = render(<Logo />);

    const root = container.firstElementChild as HTMLElement;
    expect(root.tagName).toBe("SPAN");
    expect(root).toHaveClass("inline-flex", "items-center", "gap-2");

    const plate = root.firstElementChild as HTMLElement;
    expect(plate).toHaveClass(
      "flex",
      "size-8",
      "items-center",
      "justify-center",
      "rounded-lg",
      "bg-primary",
      "text-primary-foreground",
    );
    const mark = plate.querySelector("svg");
    expect(mark).toHaveClass("size-5");
    expect(mark).toHaveAttribute("aria-hidden", "true");
    expect(mark).toHaveAttribute("stroke-width", "1.8");

    const wordmark = screen.getByText("Herdly");
    expect(wordmark).toHaveClass(
      "font-heading",
      "text-lg",
      "font-semibold",
      "tracking-tight",
    );
  });

  it("keeps the flex row when custom classes are added", () => {
    const { container } = render(<Logo className="mx-4" />);

    expect(container.firstElementChild).toHaveClass("mx-4", "inline-flex", "gap-2");
  });
});

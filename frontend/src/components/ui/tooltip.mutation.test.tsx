/**
 * Mutation-hardening for src/components/ui/tooltip.tsx: the default
 * placement contract — top side, centred alignment — that the collapsed
 * sidebar and data tables rely on, plus the caller's ability to move the
 * tip to another side and alignment.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

describe("TooltipContent placement defaults", () => {
  it("centres the tip over the trigger when nothing is specified", async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger>Delete animal</TooltipTrigger>
          <TooltipContent>Removes the animal</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );

    await user.hover(screen.getByRole("button", { name: "Delete animal" }));

    const tip = await screen.findByText("Removes the animal");
    expect(tip).toHaveAttribute("data-slot", "tooltip-content");
    expect(tip).toHaveAttribute("data-side", "top");
    expect(tip).toHaveAttribute("data-align", "center");
    expect(tip.querySelector('[data-slot="tooltip-content"] > svg, .rotate-45')).not.toBeNull();
  });

  it("honours an explicit non-default alignment", async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger>Filter</TooltipTrigger>
          <TooltipContent side="bottom" align="start">
            Narrows the list
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );

    await user.hover(screen.getByRole("button", { name: "Filter" }));

    const tip = await screen.findByText("Narrows the list");
    expect(tip).toHaveAttribute("data-side", "bottom");
    expect(tip).toHaveAttribute("data-align", "start");
  });
});

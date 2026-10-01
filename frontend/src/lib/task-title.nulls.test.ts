/**
 * Task-title null-arg semantics (2026-09-30 fresh mutation campaign): a
 * null-valued arg that the template WOULD interpolate must be skipped, so
 * the placeholder survives visibly — never rendered as the literal "null".
 */

import { describe, expect, it } from "vitest";

import { resolveTaskTitle } from "./task-title";

describe("resolveTaskTitle null interpolation args", () => {
  it("skips a null arg the template uses, leaving its placeholder unfilled", () => {
    const rendered = resolveTaskTitle(
      {
        title: "fallback",
        title_key: "feed_reorder",
        title_args: { ingredient: "Maize", qty_on_hand: null, reorder_level: 50 },
      },
      "en",
    );
    expect(rendered).toBe("Reorder Maize: {qty_on_hand} kg on hand (reorder level 50 kg)");
    expect(rendered).not.toContain("null");
  });

  it("skips an undefined arg the template uses, leaving its placeholder unfilled", () => {
    const rendered = resolveTaskTitle(
      {
        title: "fallback",
        title_key: "feed_reorder",
        // biome-ignore lint/suspicious/noExplicitAny: deliberate wire shape
        title_args: { ingredient: "Maize", qty_on_hand: undefined, reorder_level: 50 } as any,
      },
      "en",
    );
    expect(rendered).toBe("Reorder Maize: {qty_on_hand} kg on hand (reorder level 50 kg)");
    expect(rendered).not.toContain("undefined");
  });
});

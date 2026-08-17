/**
 * Unit tests for cn(): the clsx + tailwind-merge class combinator used by
 * every shadcn-style component. Covers conditional inclusion, arrays and
 * objects, and tailwind-merge conflict resolution (later class wins within
 * a conflict group, unrelated groups preserved).
 */

import { describe, expect, it } from "vitest";

import { cn, safeAppPath } from "./utils";

describe("cn", () => {
  it("joins plain class strings with a space", () => {
    expect(cn("px-2", "py-1")).toBe("px-2 py-1");
  });

  it("returns an empty string when called with nothing", () => {
    expect(cn()).toBe("");
  });

  it("drops falsy conditionals", () => {
    expect(cn("base", false && "hidden", null, undefined, 0 && "zero")).toBe(
      "base",
    );
  });

  it("includes truthy conditional expressions", () => {
    const active = true;
    expect(cn("btn", active && "btn-active")).toBe("btn btn-active");
  });

  it("flattens nested arrays of classes", () => {
    expect(cn(["px-2", ["py-1", "m-0"]])).toBe("px-2 py-1 m-0");
  });

  it("supports object syntax with boolean values", () => {
    expect(cn({ "text-red-500": true, "text-blue-500": false })).toBe(
      "text-red-500",
    );
  });

  it("resolves conflicting utilities in favour of the later class", () => {
    expect(cn("px-2", "px-4")).toBe("px-4");
    expect(cn("text-sm", "text-lg")).toBe("text-lg");
    expect(cn("bg-red-500", "bg-green-500")).toBe("bg-green-500");
  });

  it("keeps non-conflicting classes from both arguments", () => {
    expect(cn("px-2 py-1", "px-4 m-2")).toBe("py-1 px-4 m-2");
  });

  it("does not treat different utility groups as conflicts", () => {
    expect(cn("p-4", "px-2")).toBe("p-4 px-2");
    expect(cn("text-red-500", "bg-red-500")).toBe("text-red-500 bg-red-500");
  });

  it("keeps state variants distinct from the base utility", () => {
    expect(cn("px-2", "hover:px-4")).toBe("px-2 hover:px-4");
  });

  it("resolves conflicts within the same variant prefix", () => {
    expect(cn("hover:bg-red-500", "hover:bg-green-500")).toBe(
      "hover:bg-green-500",
    );
  });

  it("merges object syntax through tailwind-merge as well", () => {
    expect(cn("px-2", { "px-4": true })).toBe("px-4");
  });

  it("preserves arbitrary values that do not conflict", () => {
    expect(cn("w-[250px]", "text-white")).toBe("w-[250px] text-white");
  });

  it("resolves conflicting arbitrary values in favour of the later one", () => {
    expect(cn("w-[250px]", "w-[300px]")).toBe("w-[300px]");
  });

  it("trims redundant whitespace between joined classes", () => {
    expect(cn("  px-2  ", "py-1")).toBe("px-2 py-1");
  });
});


describe("cn — additional merge edge cases", () => {
  it("resolves conflicts written inside a single string", () => {
    expect(cn("text-red-500 text-blue-500")).toBe("text-blue-500");
  });

  it("resolves conflicts within responsive variants", () => {
    expect(cn("md:px-2", "md:px-4")).toBe("md:px-4");
  });

  it("combines arrays, objects and strings in one call", () => {
    expect(cn(["px-2", { "py-1": true }], "m-0")).toBe("px-2 py-1 m-0");
  });

  it("ignores leading falsy inputs", () => {
    expect(cn(false, "a", undefined)).toBe("a");
  });
});

describe("safeAppPath", () => {
  it("keeps canonical absolute app paths, including encoded query state", () => {
    expect(safeAppPath("/tasks")).toBe("/tasks");
    expect(safeAppPath("/tasks/7?returnTo=%2Fdashboard#details")).toBe(
      "/tasks/7?returnTo=%2Fdashboard#details",
    );
  });

  it.each([
    null,
    undefined,
    "",
    "tasks",
    "https://evil.example/tasks",
    "//evil.example/tasks",
    "/\\evil.example/tasks",
    "/\n/evil.example/tasks",
    "/tasks?next=\n//evil.example",
    "/tasks#\u0000hidden",
    "/tasks/../finance",
    "/tasks/./overdue",
    "/tasks/%2e%2e/finance",
    "/%74asks",
  ])("rejects a non-canonical or non-local destination %j", (raw) => {
    expect(safeAppPath(raw)).toBeNull();
  });

  it("fails closed for a non-string runtime value", () => {
    expect(safeAppPath(42 as never)).toBeNull();
  });
});

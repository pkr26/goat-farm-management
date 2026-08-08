import { describe, expect, it, vi } from "vitest";

import { permittedTaskActionPath } from "@/lib/task-action-access";

describe("permittedTaskActionPath", () => {
  it.each([
    ["/breeding/7/ultrasound", "breeding.manage"],
    ["/kidding/new?breeding_id=7", "kidding.manage"],
    ["/health/new?task_id=8", "health.manage"],
  ])("requires the target module permission for %s", (path, permission) => {
    expect(permittedTaskActionPath(path, () => false)).toBeNull();
    const can = vi.fn((candidate: string) => candidate === permission);
    expect(permittedTaskActionPath(path, can)).toBe(path);
    expect(can).toHaveBeenCalledWith(permission);
  });

  it("fails closed for external, malformed and unknown application paths", () => {
    const can = () => true;
    expect(permittedTaskActionPath("https://evil.example/health/new", can)).toBeNull();
    expect(permittedTaskActionPath("//evil.example/health/new", can)).toBeNull();
    expect(permittedTaskActionPath("/breeding/%2e%2e/finance", can)).toBeNull();
    expect(permittedTaskActionPath("/health/%2f../finance", can)).toBeNull();
    expect(permittedTaskActionPath("/animals/1", can)).toBeNull();
    expect(permittedTaskActionPath(null, can)).toBeNull();
  });
});

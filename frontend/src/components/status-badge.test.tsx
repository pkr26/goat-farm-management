import { describe, expect, it } from "vitest";

import { statusTone } from "@/components/status-badge";

describe("statusTone", () => {
  it("tones the insurance register lifecycle distinctly", () => {
    expect(statusTone("active")).toBe("success");
    expect(statusTone("renewed")).toBe("success");
    // Lapsed cover is attention-worthy, not a neutral chip: it must not read
    // the same as an actively renewed policy on the register.
    expect(statusTone("lapsed")).toBe("warning");
    expect(statusTone("claimed")).toBe("info");
  });

  it("keeps the established lifecycle tones", () => {
    expect(statusTone("DONE")).toBe("success");
    expect(statusTone("PENDING")).toBe("warning");
    expect(statusTone("DEAD")).toBe("destructive");
    expect(statusTone("SOMETHING_ELSE")).toBeNull();
  });
});

import { describe, expect, it } from "vitest";

import { statusTone } from "@/components/status-badge";

describe("statusTone", () => {
  it("tones the insurance register lifecycle distinctly", () => {
    expect(statusTone("active")).toBe("success");
    // "renewed" left the vocabulary entirely (renewal keeps a policy ACTIVE;
    // removed with the backend's cad1e2f3a4b5) — it renders neutral now.
    expect(statusTone("renewed")).toBeNull();
    // Lapsed cover is attention-worthy, not a neutral chip: it must not read
    // the same as an active policy on the register.
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

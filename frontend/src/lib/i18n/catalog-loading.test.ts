import { describe, expect, it, vi } from "vitest";

describe("lazy locale catalog", () => {
  it("loads Telugu through the split point and keeps English synchronous", async () => {
    vi.resetModules();
    const i18n = await import("@/lib/i18n");

    expect(i18n.languageCatalogIsLoaded("en")).toBe(true);
    expect(i18n.languageCatalogIsLoaded("te")).toBe(false);
    expect(i18n.translate("te", "tasks.title")).toBe("Tasks");

    await i18n.loadLanguageCatalog("te");

    expect(i18n.languageCatalogIsLoaded("te")).toBe(true);
    expect(i18n.translate("te", "tasks.title")).toBe("పనులు");
  });
});

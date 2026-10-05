/** Production-style provider checks: reset the modules preloaded by the
 * shared test setup, so cold imports and rejection cannot hide behind it. */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  document.documentElement.lang = "en";
  window.history.replaceState({}, "", "/");
});
afterEach(() => { cleanup(); vi.doUnmock("./te"); window.history.replaceState({}, "", "/"); });

describe("cold language provider", () => {
  it("chooses Telugu before persisting a fresh worker preference", async () => {
    window.history.replaceState({}, "", "/worker/login");
    const { LanguageProvider, useLanguage, useT } = await import("./index");
    function Child() { const { language } = useLanguage(); const t = useT(); return <output>{language}:{t("tasks.title")}</output>; }
    render(<LanguageProvider initialLanguage={null}><Child /></LanguageProvider>);
    expect(localStorage.getItem("herdly.language")).toBeNull();
    await screen.findByText("te:పనులు");
    expect(localStorage.getItem("herdly.language")).toBe("te");
    expect(document.documentElement.lang).toBe("te");
  });

  it.each(["local", "cookie"])("preserves an intentional English %s preference on a worker", async (source) => {
    window.history.replaceState({}, "", "/worker/login");
    if (source === "local") localStorage.setItem("herdly.language", "en");
    const { LanguageProvider, useLanguage } = await import("./index");
    function Child() { return <output>{useLanguage().language}</output>; }
    render(<LanguageProvider initialLanguage={source === "cookie" ? "en" : null}><Child /></LanguageProvider>);
    await screen.findByText("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("offers recovery after a rejected lazy catalog and retries on reconnection", async () => {
    let offline = true;
    let attempts = 0;
    vi.doMock("./te", () => ({ get default() {
      attempts += 1;
      if (offline) throw new Error("offline locale chunk");
      return { "tasks.title": "పనులు" };
    } }));
    const { LanguageProvider, useT } = await import("./index");
    function Child() { return <output>{useT()("tasks.title")}</output>; }
    localStorage.setItem("herdly.language", "te");
    render(<LanguageProvider initialLanguage="te"><Child /></LanguageProvider>);
    await screen.findByRole("alert");
    expect(screen.queryByText("Tasks")).not.toBeInTheDocument();
    const firstAttempts = attempts;
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await waitFor(() => expect(attempts).toBeGreaterThan(firstAttempts));
    offline = false;
    await act(async () => window.dispatchEvent(new Event("online")));
    await screen.findByText("పనులు");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(document.documentElement.lang).toBe("te");
  });

  it("allows English recovery without network or changing language on late stale completion", async () => {
    vi.doMock("./te", () => { throw new Error("missing chunk"); });
    const { LanguageProvider, useLanguage } = await import("./index");
    function Child() { return <output>{useLanguage().language}</output>; }
    render(<LanguageProvider initialLanguage="te"><Child /></LanguageProvider>);
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(screen.getByText("en")).toBeInTheDocument();
    expect(localStorage.getItem("herdly.language")).toBe("en");
  });

  it("renders dates and enum labels in the context language on the very same transition", async () => {
    const { LanguageProvider, useLanguage, loadLanguageCatalog } = await import("./index");
    const { formatDate } = await import("@/lib/format");
    const { enumLabel } = await import("@/lib/enum-labels");
    const { getActiveLanguage } = await import("@/lib/active-language");
    await loadLanguageCatalog("te");
    const renders: string[][] = [];
    function Child() {
      const { language, setLanguage } = useLanguage();
      renders.push([language, getActiveLanguage(), formatDate("2026-08-05"), enumLabel("sex", "F")]);
      return <><output>{formatDate("2026-08-05")}</output><button onClick={() => setLanguage(language === "en" ? "te" : "en")}>toggle</button></>;
    }
    render(<LanguageProvider initialLanguage="te"><Child /></LanguageProvider>);
    expect(screen.getByText(formatDate("2026-08-05", "te"))).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "toggle" }));
    expect(screen.getByText("5 Aug 2026")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "toggle" }));
    expect(screen.getByText(formatDate("2026-08-05", "te"))).toBeInTheDocument();
    for (const [context, active, date, sex] of renders) {
      expect(active).toBe(context);
      expect(date).toBe(formatDate("2026-08-05", context as "en" | "te"));
      expect(sex).toBe(enumLabel("sex", "F", context as "en" | "te"));
    }
  });
});

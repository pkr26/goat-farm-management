import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY } from "./config";

afterEach(() => { cleanup(); vi.doUnmock("./te"); });

it("recovers when connectivity returns as the failed-catalog message enters the public DOM", async () => {
  vi.resetModules();
  localStorage.clear();
  document.documentElement.lang = "en";
  window.history.replaceState({}, "", "/");
  let offline = true;
  const catalog = await vi.importActual<typeof import("./te")>("./te");
  vi.doMock("./te", () => ({ get default() {
    if (offline) throw new Error("locale chunk unavailable");
    return catalog.default;
  } }));
  const { LanguageProvider, useLanguage, useT } = await import("./index");
  function Child() {
    const { setLanguage } = useLanguage();
    const t = useT();
    return <><output>{t("tasks.title")}</output><button onClick={() => setLanguage("te")}>Use Telugu</button></>;
  }
  let restorations = 0;
  const observer = new MutationObserver(() => {
    if (!document.querySelector('[role="alert"]') || restorations) return;
    restorations += 1;
    offline = false;
    window.dispatchEvent(new Event("online"));
  });
  observer.observe(document.body, { childList: true, subtree: true });
  try {
    render(<LanguageProvider initialLanguage="en"><Child /></LanguageProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Use Telugu" }));
    await waitFor(() => expect(restorations).toBe(1));
    await screen.findByText("పనులు");
    expect(restorations).toBe(1);
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("te");
    expect(screen.queryByRole("alert")).toBeNull();
  } finally {
    observer.disconnect();
  }
});

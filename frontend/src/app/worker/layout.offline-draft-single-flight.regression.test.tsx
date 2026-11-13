import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IDBFactory } from "fake-indexeddb";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";

import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { LanguageProvider } from "@/lib/i18n";
import { drainWorkerOutbox, persistOfflineWorkerDraft, persistWorkerOperation, readWorkerOutbox } from "@/lib/worker-outbox";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { server } from "@/test/msw-server";
import { WorkerShell } from "./layout";

const { success, error } = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success, error } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }), usePathname: () => "/worker" }));

it.each(["confirm", "discard", "retry", "dismiss"] as const)("settles a native double %s of a saved receipt once without a false failure", async (action) => {
  vi.stubGlobal("indexedDB", new IDBFactory());
  vi.stubGlobal("structuredClone", (value: unknown) => JSON.parse(JSON.stringify(value)));
  localStorage.clear(); sessionStorage.clear(); localStorage.setItem("herdly.language", "en");
  const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  const scope = { actorScope: "1", farmScope: "1" };
  let draft;
  if (action === "retry" || action === "dismiss") {
    setAccessToken("review-producer", 1); setCurrentFarmId("1");
    server.use(http.post("/api/tasks/41/complete", () => HttpResponse.json({ detail: "Conflict" }, { status: 409 })));
    draft = await persistWorkerOperation("/api/tasks/41/complete", undefined, scope);
    await expect(drainWorkerOutbox(scope)).resolves.toMatchObject({ replayed: 0, rejected: 1, remaining: 1 });
  } else draft = await persistOfflineWorkerDraft("/api/tasks/41/complete", undefined, scope);
  const neighbor = await persistOfflineWorkerDraft("/api/tasks/42/complete", undefined, scope);
  setAccessToken(null); setCurrentFarmId(null); success.mockClear(); error.mockClear();
  const client = createTestQueryClient();
  const view = renderWithProviders(<LanguageProvider><WorkerShell><p>Current duty board</p></WorkerShell></LanguageProvider>, client);
  try {
    await view.waitForAuthIdle();
    act(() => { online.mockReturnValue(false); window.dispatchEvent(new Event("offline")); });
    await userEvent.setup().click(await screen.findByText("Saved duties and delivery receipts"));
    let button = await screen.findByTestId(action === "retry" || action === "dismiss"
      ? `worker-${action}-review-${draft.id}` : `worker-${action}-offline-${draft.id}`);
    if (action === "dismiss") {
      await userEvent.setup().click(button);
      button = await screen.findByTestId("worker-dismiss-review-confirm");
    }
    expect(button).toBeEnabled();
    await act(async () => {
      button.click(); button.click();
      await readWorkerOutbox(scope); await readWorkerOutbox(scope);
    });
    await waitFor(() => expect(success).toHaveBeenCalled());
    expect(success).toHaveBeenCalledTimes(1); expect(error).not.toHaveBeenCalled();
    const records = await readWorkerOutbox(scope);
    if (action === "confirm" || action === "retry") {
      const confirmed = records.find(record => record.id === draft.id);
      expect(confirmed).toMatchObject({ state: "pending" }); expect(confirmed?.reason).toBeUndefined();
    }
    else expect(records.find(record => record.id === draft.id)).toBeUndefined();
    expect(records.find(record => record.id === neighbor.id)).toMatchObject({ state: "review", reason: "offline-untrusted" });
    expect(screen.getByTestId(`worker-confirm-offline-${neighbor.id}`)).toBeEnabled();
  } finally {
    view.unmount(); client.clear(); await readWorkerOutbox(scope);
    online.mockRestore(); setAccessToken(null); setCurrentFarmId(null);
    localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals();
  }
});

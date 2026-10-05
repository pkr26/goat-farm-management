import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/i18n";
import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { endOfflineShift, readOfflineShift, saveOfflineShift } from "@/lib/worker-offline-shift";
import { persistWorkerOperation, readWorkerOutbox, settleWorkerOperation } from "@/lib/worker-outbox";
import OfflineWorkerPage from "./page";

const { replace, toastInfo, toastError, auth } = vi.hoisted(() => ({
  replace: vi.fn(), toastInfo: vi.fn(), toastError: vi.fn(),
  auth: { user: null as { id: number } | null, loading: false },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => auth }));
vi.mock("sonner", () => ({ toast: { info: toastInfo, error: toastError } }));

const shift = {
  actorScope: "7", farmScope: "3", workerName: "Ravi", farmName: "East Farm",
  tasks: [
    { id: 41, title: "Clean pen", dueDate: "2026-10-03", canComplete: true, canSkip: true },
    { id: 42, title: "Vaccination form", dueDate: "2026-10-03", canComplete: false, canSkip: false },
  ],
};

beforeEach(() => {
  vi.stubGlobal("indexedDB", new IDBFactory());
  vi.stubGlobal("structuredClone", (value: unknown) => JSON.parse(JSON.stringify(value)));
  sessionStorage.clear(); localStorage.clear(); localStorage.setItem("herdly.language", "en");
  replace.mockReset(); toastInfo.mockReset(); toastError.mockReset();
  auth.user = null; auth.loading = false;
  setAccessToken("worker-session", 7); setCurrentFarmId("3");
});

const mount = () => render(<LanguageProvider><OfflineWorkerPage /></LanguageProvider>);
afterEach(() => vi.unstubAllGlobals());

it("requires a current saved shift and preserves the reconnect route", async () => {
  mount();
  expect(await screen.findByText(/No current saved shift is available/)).toBeVisible();
  expect(screen.getByRole("link", { name: "Sign in to review offline drafts" })).toHaveAttribute("href", "/worker/login");
  expect(screen.queryByRole("button", { name: "Complete" })).toBeNull();
});

it("records a manual duty as an untrusted draft and never completes linked forms", async () => {
  await saveOfflineShift(shift); setAccessToken(null); setCurrentFarmId(null);
  const first = mount();
  expect(await screen.findByText("East Farm · Ravi")).toBeVisible();
  expect(screen.getByText(/This duty needs its linked form/)).toBeVisible();
  fireEvent.click(screen.getByTestId("offline-complete-41"));
  await waitFor(() => expect(toastInfo).toHaveBeenCalledWith(
    "Draft saved — sign in as this worker to review and confirm it before sending.",
  ));
  expect(await readWorkerOutbox(shift)).toMatchObject([{ path: "/api/tasks/41/complete", state: "review", reason: "offline-untrusted", actorScope: "7", farmScope: "3" }]);
  first.unmount(); mount();
  expect(await screen.findByText("Needs review — kept on this tablet")).toBeVisible();
  expect(screen.queryByTestId("offline-complete-41")).toBeNull();
});

it("retains skipped duties with their reason and original scope", async () => {
  await saveOfflineShift(shift); mount();
  fireEvent.click(await screen.findByTestId("offline-skip-41"));
  await waitFor(() => expect(toastInfo).toHaveBeenCalledWith(
    "Draft saved — sign in as this worker to review and confirm it before sending.",
  ));
  const rows = await readWorkerOutbox(shift);
  expect(rows).toHaveLength(1);
  expect(rows[0]).toMatchObject({ path: "/api/tasks/41/skip", state: "review", reason: "offline-untrusted", actorScope: "7", farmScope: "3" });
  expect(JSON.parse(rows[0]!.body!)).toHaveProperty("reason");
});

it("keeps review receipts visible without offering duplicate actions", async () => {
  await saveOfflineShift(shift);
  const operation = await persistWorkerOperation("/api/tasks/41/complete", undefined, shift);
  await settleWorkerOperation(operation.id, "review", "HTTP 409", 409);
  mount();
  expect(await screen.findByText("Needs review — kept on this tablet")).toBeVisible();
  expect(screen.queryByTestId("offline-complete-41")).toBeNull();
});

it("ends the offline capability while retaining pending work for its original worker", async () => {
  await saveOfflineShift(shift);
  await persistWorkerOperation("/api/tasks/41/complete", undefined, shift);
  mount(); fireEvent.click(await screen.findByRole("button", { name: "End shift" }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/worker/login"));
  expect(await readOfflineShift()).toBeNull();
  expect(await readWorkerOutbox(shift)).toHaveLength(1);
});

it("refuses an action when the shift ends after the board was displayed", async () => {
  await saveOfflineShift(shift); mount();
  const complete = await screen.findByTestId("offline-complete-41");
  await endOfflineShift(); fireEvent.click(complete);
  expect(await screen.findByText(/No current saved shift is available/)).toBeVisible();
  expect(await readWorkerOutbox(shift)).toEqual([]);
  expect(toastInfo).not.toHaveBeenCalled();
});

it("shows storage failure without acknowledging a saved action", async () => {
  await saveOfflineShift(shift); mount();
  const complete = await screen.findByTestId("offline-complete-41");
  vi.stubGlobal("indexedDB", undefined); fireEvent.click(complete);
  await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("alert")).toBeVisible();
  expect(toastInfo).not.toHaveBeenCalled();
});

it("returns an authenticated worker to the live board", async () => {
  auth.user = { id: 7 }; mount();
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/worker"));
});

it("leaves primary landmark ownership to the enclosing worker shell", async () => {
  render(<LanguageProvider><main><OfflineWorkerPage /></main></LanguageProvider>);
  await screen.findByRole("heading", { level: 1 });
  expect(screen.getAllByRole("main")).toHaveLength(1);
});

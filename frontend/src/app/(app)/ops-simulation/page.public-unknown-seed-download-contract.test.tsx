import { MutationCache, QueryClient } from "@tanstack/react-query";
import type { runDailyOpsSimulationApiOpsSimRunPostResponseSuccess } from "@/api/generated/endpoints";
import { fireEvent, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import type { DailyOpsResult } from "@/api/generated/models";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/msw-server";
import OpsSimulationPage from "./page";
const { error } = vi.hoisted(() => ({ error: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error, success: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/ops-simulation", useSearchParams: () => new URLSearchParams(), useParams: () => ({}) }));
const result: DailyOpsResult = {
  model_version: "1.0.0", species: "GOAT", start_date: "2026-09-03", horizon_days: 7, seed: 11, head_start: 4,
  days: [{ day: 1, date: "2026-09-03" }], journeys: [], transition_counts: [], explanations: [], notes: [],
  totals: { feed_kg_by_recipe: {}, tasks_by_category: {}, tasks_by_role: {}, vet_tasks_by_building: {}, building_days: {}, services: 0, conceptions: 0, failed_services: 0, kids_born_alive: 0, kids_born_dead: 0, deaths: 0, culls: 0, sales: 0, moves: 0 },
};
it.each([{ description: "unknown nonfinite metadata", seed: Number.NaN, nameSeed: "unknown" }, { description: "known seed zero", seed: 0, nameSeed: "0" }, { description: "known finite seed", seed: 11, nameSeed: "11" }])("labels downloaded seed metadata honestly for $description restored by a public mutation consumer", async ({ seed, nameSeed }) => {
  const startDate = "2026-09-03";
  const callbackSeed: number[] = [];
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false }, mutations: { retry: false } },
    mutationCache: new MutationCache({ onSuccess(data) {
      // A typed public consumer restores numeric metadata after real HTTP;
      // this malformed-cache profile never claims JSON transports NaN.
      const response = data as runDailyOpsSimulationApiOpsSimRunPostResponseSuccess;
      response.data.result.seed = seed;
      callbackSeed.push(response.data.result.seed);
    } }),
  });
  const ledger = "# Operations ledger\nA retained immutable run.\n";
  server.use(http.post("/api/ops-sim/run", () => HttpResponse.json({ result: { ...result, start_date: startDate, seed: 11 }, ledger })));
  const createDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
  const revokeDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
  const downloads: { name: string; href: string }[] = [];
  const blobs: Blob[] = [];
  const revoke = vi.fn();
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: (blob: Blob) => { blobs.push(blob); return "blob:ledger-contract"; } });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revoke });
  const observeDownload = (event: MouseEvent) => {
    if (event.target instanceof HTMLAnchorElement && event.target.href === "blob:ledger-contract") {
      downloads.push({ name: event.target.download, href: event.target.href }); event.preventDefault();
    }
  };
  document.addEventListener("click", observeDownload, true);
  const view = renderWithProviders(<OpsSimulationPage />, queryClient);
  try {
    await view.waitForAuthIdle();
    fireEvent.click(await screen.findByRole("button", { name: /^Run 90 days/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Download the day-by-day ledger/ }));
    expect(downloads).toEqual([{ name: `ops-simulation-${startDate}-seed-${nameSeed}.md`, href: "blob:ledger-contract" }]);
    expect(callbackSeed).toEqual([seed]);
    expect(blobs).toHaveLength(1);
    expect(blobs[0].type).toBe("text/markdown;charset=utf-8");
    const text = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(reader.error); reader.readAsText(blobs[0]);
    });
    expect(text).toBe(ledger);
    expect(revoke).toHaveBeenCalledWith("blob:ledger-contract");
  } finally {
    view.unmount(); view.queryClient.clear(); document.removeEventListener("click", observeDownload, true);
    if (createDescriptor) Object.defineProperty(URL, "createObjectURL", createDescriptor); else delete (URL as { createObjectURL?: unknown }).createObjectURL;
    if (revokeDescriptor) Object.defineProperty(URL, "revokeObjectURL", revokeDescriptor); else delete (URL as { revokeObjectURL?: unknown }).revokeObjectURL;
  }
});

import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";

import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { LanguageProvider } from "@/lib/i18n";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { server } from "@/test/msw-server";
import WorkerLoginPage from "./page";

const { router } = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/worker/login" }));

it("keeps the first worker visible after two same-task native Previous activations", async () => {
  sessionStorage.clear(); localStorage.clear();
  localStorage.setItem("herdly.language", "en"); localStorage.setItem("herdly.tabletFarm", "3");
  setAccessToken(null); setCurrentFarmId(null);
  server.use(
    http.post("/api/auth/refresh", () => HttpResponse.json({ detail: "No session" }, { status: 401 })),
    http.get("/api/auth/worker-roster", ({ request }) => {
      const params = new URL(request.url).searchParams;
      const after = Number(params.get("after_membership_id"));
      if (params.get("farm_id") !== "3") return HttpResponse.json({ detail: "Wrong tablet farm" }, { status: 422 });
      const remaining = [{ membership_id: 1, display_name: "Alice" }, { membership_id: 2, display_name: "Bob" }].filter((worker) => worker.membership_id > after);
      return HttpResponse.json({ items: remaining.slice(0, 1), next_after_membership_id: remaining.length > 1 ? remaining[0].membership_id : null });
    }),
  );
  const client = createTestQueryClient();
  const view = renderWithProviders(<LanguageProvider><WorkerLoginPage /></LanguageProvider>, client);
  try {
    await view.waitForAuthIdle();
    expect(await screen.findByRole("button", { name: "Alice" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Bob" })).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByRole("button", { name: "Bob" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Alice" })).toBeNull();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
    const previous = screen.getByRole("button", { name: "Previous" });
    await act(async () => { previous.click(); previous.click(); });
    expect(await screen.findByRole("button", { name: "Alice" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Bob" })).toBeNull();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  } finally {
    view.unmount(); client.clear();
    setAccessToken(null); setCurrentFarmId(null); sessionStorage.clear(); localStorage.clear();
  }
});

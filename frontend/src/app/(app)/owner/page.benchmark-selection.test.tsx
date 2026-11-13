import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import OwnerPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/owner", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));

it("marks exactly the chosen benchmark window and requests that same duration", async () => {
  const requestedDays: number[] = [];
  server.use(
    http.get("/api/owner/overview", () => HttpResponse.json({ farms: [] })),
    http.get("/api/owner/benchmarks", ({ request }) => {
      const days = Number(new URL(request.url).searchParams.get("days"));
      requestedDays.push(days);
      return HttpResponse.json({ days, farms: [] });
    }),
  );
  renderWithProviders(<OwnerPage />);
  const controls = within(await screen.findByRole("group", { name: "Benchmark window" }));
  const expectSelection = (selected: number) => {
    for (const days of [30, 90, 365]) expect(controls.getByRole("button", { name: `${days}d` })).toHaveAttribute("aria-pressed", String(days === selected));
  };
  await waitFor(() => expect(requestedDays).toEqual([90]));
  expectSelection(90);
  const user = userEvent.setup();
  for (const days of [30, 365, 90]) {
    await user.click(controls.getByRole("button", { name: `${days}d` }));
    expectSelection(days);
    await waitFor(() => expect(requestedDays.at(-1)).toBe(days));
  }
});

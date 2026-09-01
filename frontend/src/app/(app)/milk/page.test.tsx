/**
 * Milk page species behaviour: the parlour is a dairy module, so a goat farm
 * (which still holds milk.view through the owner role) gets the whole page
 * hidden and never fires a milk query, while a buffalo dairy sees the
 * farm-vocabulary copy and Indian-grouped litre totals.
 */

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server, TEST_FARMS, ALL_PERMISSIONS, permissionsHandler } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import MilkPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/milk",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const DAIRY_FARM = { ...TEST_FARMS[0], farm_type: "BUFFALO_DAIRY" };

function dairyFarmHandler() {
  return http.get("/api/auth/farms", () => HttpResponse.json([DAIRY_FARM]));
}

const SUMMARY = {
  days: 30,
  total_litres: 2450.5,
  avg_daily_litres: 81.7,
  avg_fat_pct: 6.9,
  daily: [],
  animals: [
    {
      animal_id: 3,
      animal_tag: "BUF-003",
      total_litres: 30000.4,
      avg_daily_litres: 8.2,
      days_recorded: 30,
      avg_fat_pct: 7.1,
    },
  ],
};

describe("MilkPage — species gate", () => {
  it("hides the parlour for a goat farm without querying milk APIs", async () => {
    let milkCalls = 0;
    server.use(
      http.get("/api/milk", () => {
        milkCalls += 1;
        return HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 });
      }),
      http.get("/api/milk/summary", () => {
        milkCalls += 1;
        return HttpResponse.json(SUMMARY);
      }),
    );

    // Default MSW session: one GOAT farm, owner permissions.
    renderWithProviders(<MilkPage />);

    expect(
      await screen.findByText("Milk recording is for dairy farms."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Today's herd total")).not.toBeInTheDocument();
    // The empty state only renders after permissions resolved, so the queries
    // have had every opportunity to start — and must not have.
    expect(milkCalls).toBe(0);
  });
});

describe("MilkPage — fat test gate", () => {
  it("disables the fat field for a recorder without the quality permission", async () => {
    server.use(
      dairyFarmHandler(),
      permissionsHandler(
        ALL_PERMISSIONS.filter((code) => code !== "milk.quality"),
      ),
      http.get("/api/milk/summary", () => HttpResponse.json(SUMMARY)),
      http.get("/api/milk", () =>
        HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 }),
      ),
    );

    renderWithProviders(<MilkPage />);

    const fatInput = await screen.findByLabelText("Fat % (quality role only)");
    expect(fatInput).toBeDisabled();
    expect(
      screen.getByText(
        "Only the milk quality / manager roles record fat tests. A re-recorded yield keeps its tested fat as-is.",
      ),
    ).toBeInTheDocument();
    // Litres stay recordable: the parlour recorder can still do their job.
    expect(screen.getByLabelText("Litres")).toBeEnabled();
  });

  it("keeps the fat field editable for a role holding the quality permission", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => HttpResponse.json(SUMMARY)),
      http.get("/api/milk", () =>
        HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 }),
      ),
    );

    renderWithProviders(<MilkPage />);

    expect(await screen.findByLabelText("Fat % (optional)")).toBeEnabled();
  });
});

describe("MilkPage — dairy vocabulary and litre grouping", () => {
  it("uses the female-parent noun from the farm vocabulary", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => HttpResponse.json(SUMMARY)),
      http.get("/api/milk", () =>
        HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 }),
      ),
    );
    const user = userEvent.setup();

    renderWithProviders(<MilkPage />);

    // Picker label, and the empty-animal validation copy.
    expect(await screen.findByLabelText("Milking buffalo")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Record yield" }));
    expect(
      await screen.findByText("Pick the milking buffalo this reading belongs to."),
    ).toBeInTheDocument();
    expect(await screen.findByText("Per-milking buffalo averages (30 days)")).toBeInTheDocument();
  });

  it("groups litre totals with Indian digit grouping", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => HttpResponse.json(SUMMARY)),
      http.get("/api/milk", () =>
        HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 }),
      ),
    );

    renderWithProviders(<MilkPage />);

    expect(await screen.findByText("2,451 L")).toBeInTheDocument();
    expect(screen.getByText("82 L/day average")).toBeInTheDocument();
    expect(screen.getByText("30,000")).toBeInTheDocument();
    expect(screen.queryByText("30000")).not.toBeInTheDocument();
  });
});

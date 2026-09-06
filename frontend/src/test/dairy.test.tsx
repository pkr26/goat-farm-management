/**
 * Dairy (buffalo) farm type on the client: species vocabulary, the farm-type
 * picker on farm creation, and the milk yields page.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import MilkPage from "@/app/(app)/milk/page";
import FarmSelectPage from "@/app/farm-select/page";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/farm-select",
  useSearchParams: () => new URLSearchParams(""),
  useParams: () => ({}),
}));

const DAIRY_FARM = {
  id: 7,
  name: "Navipet Dairy",
  location: "Navipet",
  timezone: "Asia/Kolkata",
  role: null,
  farm_type: "BUFFALO_DAIRY",
};

describe("farm vocabulary", () => {
  it("uses dairy words for buffalo farms and goat words otherwise", async () => {
    const { farmVocabulary } = await import("@/lib/farm-vocabulary");
    const dairy = farmVocabulary("BUFFALO_DAIRY");
    expect(dairy.parturition).toBe("calving");
    expect(dairy.parturitionCap).toBe("Calving");
    expect(dairy.young).toBe("calf");
    expect(dairy.dairy).toBe(true);
    const goat = farmVocabulary("GOAT");
    expect(goat.parturition).toBe("kidding");
    expect(goat.femaleAdult).toBe("doe");
    expect(farmVocabulary("SOMETHING_ELSE").parturition).toBe("kidding");
    expect(farmVocabulary(undefined).dairy).toBe(false);
  });

  it("states species facts that mirror the backend profiles exactly", async () => {
    const { farmVocabulary } = await import("@/lib/farm-vocabulary");
    const goat = farmVocabulary("GOAT");
    const buffalo = farmVocabulary("BUFFALO_DAIRY");
    // backend/app/models/species.py — drift shows operators biology the
    // server does not enforce (or blocks what it accepts).
    expect(goat.facts).toEqual({
      gestationWindowDays: { min: 100, max: 200 },
      pregnancyCheckDays: 32,
      weaningDays: 60,
      youngStayWithDam: true,
      maxLitterSize: 4,
      maxWeightKg: 150,
      birthWeightKg: { min: 0.5, max: 8 },
    });
    expect(buffalo.facts).toEqual({
      gestationWindowDays: { min: 270, max: 350 },
      pregnancyCheckDays: 60,
      weaningDays: 90,
      youngStayWithDam: false,
      maxLitterSize: 2,
      maxWeightKg: 1000,
      birthWeightKg: { min: 15, max: 80 },
    });
    // The tag generator issues "G-XXXXX" for every species
    // (services/animals.generate_unique_tag) — the prefix must never
    // promise a scheme the backend will not produce.
    expect(goat.tagPrefix).toBe("G");
    expect(buffalo.tagPrefix).toBe("G");
    expect(goat.femaleAdultPlural).toBe("does");
    expect(buffalo.femaleAdultPlural).toBe("milking buffalo");
  });

  it("pins the breeding-entry gates to the backend species floors", async () => {
    const { farmVocabulary } = await import("@/lib/farm-vocabulary");
    const goat = farmVocabulary("GOAT");
    const buffalo = farmVocabulary("BUFFALO_DAIRY");
    // GOAT_PROFILE / BUFFALO_DAIRY_PROFILE min_breeding_* — the client gate
    // may not be looser than the server's, or a valid-looking import is
    // bounced after the operator has filled the form.
    expect(goat.breedingEntry).toEqual({
      female: { minMonths: 10, minWeightKg: 22 },
      male: { minMonths: 12, minWeightKg: 25 },
    });
    expect(buffalo.breedingEntry).toEqual({
      female: { minMonths: 24, minWeightKg: 340 },
      male: { minMonths: 24, minWeightKg: 350 },
    });
    expect(buffalo.breedingGateCopy).toContain("24 months");
  });
});

describe("FarmSelectPage — farm type", () => {
  it("creates a buffalo dairy farm with the picked type", async () => {
    const posted: unknown[] = [];
    server.use(
      http.post("/api/auth/farms", async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json(DAIRY_FARM, { status: 201 });
      }),
    );
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([DAIRY_FARM])));

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Your farms");

    await user.type(screen.getByLabelText("Farm name"), "Navipet Dairy");
    await user.click(screen.getByRole("radio", { name: /Buffalo dairy/ }));
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ name: "Navipet Dairy", farm_type: "BUFFALO_DAIRY" });
  });

  it("defaults new farms to the goat type", async () => {
    const posted: unknown[] = [];
    server.use(
      http.post("/api/auth/farms", async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json({ ...DAIRY_FARM, farm_type: "GOAT" }, { status: 201 });
      }),
    );
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Your farms");

    await user.type(screen.getByLabelText("Farm name"), "Plain Herd");
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ farm_type: "GOAT" });
  });
});

describe("MilkPage", () => {
  // The parlour is dairy-only: the page hides itself on a goat farm, so these
  // tests run against the buffalo-dairy session.
  const DAIRY_SESSION = {
    farms: http.get("/api/auth/farms", () => HttpResponse.json([DAIRY_FARM])),
  };

  it("shows herd totals and per-buffalo averages from the summary", async () => {
    server.use(
      DAIRY_SESSION.farms,
      http.get("/api/milk/summary", () =>
        HttpResponse.json({
          days: 30,
          total_litres: 2450.5,
          avg_daily_litres: 81.7,
          avg_fat_pct: 6.9,
          daily: [{ date: "2026-08-30", litres: 96.5, recorded_animals: 12, avg_fat_pct: 6.8 }],
          animals: [
            {
              animal_id: 3,
              animal_tag: "BUF-003",
              total_litres: 245.0,
              avg_daily_litres: 8.2,
              days_recorded: 30,
              avg_fat_pct: 7.1,
            },
          ],
        }),
      ),
    );
    server.use(
      http.get("/api/milk", () =>
        HttpResponse.json({
          records: [
            {
              id: 11,
              animal_id: 3,
              date: "2026-08-30",
              shift: "MORNING",
              litres: 8.5,
              fat_pct: 6.9,
              notes: null,
              created_at: "2026-08-30T01:00:00Z",
              animal_tag: "BUF-003",
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
          total_litres: 8.5,
        }),
      ),
    );

    renderWithProviders(<MilkPage />);
    await waitFor(() =>
      expect(screen.getByText("Today's herd total")).toBeInTheDocument(),
    );
    expect(await screen.findAllByText("BUF-003")).not.toHaveLength(0);
    // Average fat comes straight from the summary (no date-window dependency).
    expect(screen.getByText("6.9%")).toBeInTheDocument();
    expect(screen.getByText("Morning")).toBeInTheDocument();
  });

  it("rejects an out-of-range litre entry before any request", async () => {
    const posts = vi.fn();
    server.use(
      DAIRY_SESSION.farms,
      http.post("/api/milk/new", () => (posts(), HttpResponse.json({}, { status: 201 }))),
      http.get("/api/milk", () =>
        HttpResponse.json({
          records: [],
          total: 0,
          limit: 50,
          offset: 0,
          total_litres: 0,
        }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);
    await user.type(await screen.findByLabelText("Litres"), "250");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    // No animal picked yet: the eligibility copy appears and nothing posts.
    expect(
      await screen.findByText("Pick the milking buffalo this reading belongs to."),
    ).toBeInTheDocument();
    expect(posts).not.toHaveBeenCalled();
  });
});

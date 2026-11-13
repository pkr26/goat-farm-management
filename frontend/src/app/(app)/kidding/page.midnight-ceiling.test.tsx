import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import type { BreedingRecordOut } from "@/api/generated/models";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { addDays } from "@/lib/format";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import KiddingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/kidding", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const CEILING = "2026-10-08";
function makeBreeding(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    semen_sire_name: null,
    breeding_date: addDays(CEILING, -farmVocabulary.facts.gestationWindowDays.max),
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: addDays(CEILING, -160),
    ultrasound_result_date: null,
    ultrasound_done: true,
    pregnant: true,
    kid_count_detected: 2,
    expected_kidding_date: CEILING,
    outcome: "CONFIRMED_PREGNANT",
    loss_date: null,
    loss_cause: null,
    loss_notes: null,
    loss_recorded_by_id: null,
    loss_recorded_at: null,
    created_at: "2026-01-01T00:00:00Z",
    has_kidding: false,
    doe_tag: "G-010",
    buck_tag: "G-020",
    ...overrides,
  };
}

it("keeps the opened default and every date ceiling within gestation when the farm clock crosses midnight", async () => {
  const beforeMidnight = new Date("2026-10-08T18:29:59.999Z");
  const afterMidnight = new Date("2026-10-08T18:30:00.000Z");
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(beforeMidnight);
  const record = makeBreeding({ id: 12 });
  server.use(http.get("/api/kidding", () => HttpResponse.json({
    records: [], upcoming: [record], upcoming_total: 1, upcoming_limit: 25, upcoming_offset: 0,
    overdue: [], overdue_total: 0, overdue_limit: 25, overdue_offset: 0,
    total: 0, limit: 50, offset: 0,
  })));
  const reads: { instant: string; day: string }[] = [];
  const observedMaxima: string[] = [];
  const observer = new MutationObserver((changes) => {
    for (const change of changes) {
      if (change.type === "attributes" && change.target instanceof HTMLInputElement && change.target.id === "kidding_date") {
        if (change.oldValue) observedMaxima.push(change.oldValue);
        if (change.target.max) observedMaxima.push(change.target.max);
      }
    }
    const field = document.querySelector<HTMLInputElement>("#kidding_date");
    if (field?.max) observedMaxima.push(field.max);
  });
  observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["max"], attributeOldValue: true });
  const nativeParts = Intl.DateTimeFormat.prototype.formatToParts;
  let parts: ReturnType<typeof vi.spyOn> | undefined;
  try {
    renderWithProviders(<KiddingPage />);
    const button = (await screen.findAllByRole("button", { name: "Record kidding" }))[0];
    // Advance the actual Date clock by1ms after two real calendar reads. Intl
    // computes every date normally; no farmToday or date-result mock is used.
    parts = vi.spyOn(Intl.DateTimeFormat.prototype, "formatToParts").mockImplementation(function (this: Intl.DateTimeFormat, date) {
      const value = nativeParts.call(this, date);
      const fields = Object.fromEntries(value.map((part) => [part.type, part.value]));
      reads.push({ instant: new Date(date as number).toISOString(), day: `${fields.year}-${fields.month}-${fields.day}` });
      if (reads.length === 2) vi.setSystemTime(afterMidnight);
      return value;
    });
    fireEvent.click(button);
    const input = await screen.findByLabelText("Kidding date *") as HTMLInputElement;
    await waitFor(() => expect(reads.some((read) => read.day === "2026-10-09")).toBe(true));
    expect(reads.slice(0, 2).map((read) => read.instant)).toEqual([beforeMidnight.toISOString(), beforeMidnight.toISOString()]);
    expect(input).toHaveValue(CEILING);
    expect(input.max).toBe(CEILING);
    expect(observedMaxima.length).toBeGreaterThan(0);
    expect(observedMaxima.every((max) => max <= CEILING)).toBe(true);
  } finally { parts?.mockRestore(); observer.disconnect(); vi.useRealTimers(); }
});

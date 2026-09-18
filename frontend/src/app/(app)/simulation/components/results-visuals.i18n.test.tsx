import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "@/lib/i18n";
import { renderWithProviders } from "@/test/render";

import { RiskBandTable } from "./results-visuals";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

afterEach(() => {
  localStorage.clear();
  document.documentElement.lang = "en";
});

describe("RiskBandTable Telugu localization", () => {
  it("translates its extracted result-table chrome", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    renderWithProviders(
      <LanguageProvider>
        <RiskBandTable
          herd={{ p5: [1], p25: [1.5], p50: [2], p75: [2.5], p95: [3] }}
          liquidity={{ p5: [100], p25: [150], p50: [200], p75: [250], p95: [300] }}
        />
      </LanguageProvider>,
    );

    expect(await screen.findByText("వార్షిక అనిశ్చితి తనిఖీ కేంద్రాలు")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "మంద P50" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "నగదు P95" })).toBeInTheDocument();
    expect(screen.queryByText("Annual uncertainty checkpoints")).not.toBeInTheDocument();
  });
});

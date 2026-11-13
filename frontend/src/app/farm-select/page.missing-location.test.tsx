import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";

import type { FarmOut } from "@/api/generated/models";
import { LanguageProvider } from "@/lib/i18n";
import { server, TEST_FARMS } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import FarmSelectPage from "./page";

const { router } = vi.hoisted(() => ({ router: { push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => router, usePathname: () => "/farm-select",
  useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));

it.each([
  { location: null, expected: "—", caseName: "null" },
  { location: "", expected: "—", caseName: "empty" },
  { location: "Solapur", expected: "Solapur", caseName: "provided" },
])("identifies a missing farm location without hiding the role: $caseName", async ({ location, expected }) => {
  localStorage.setItem("herdly.language", "en");
  const farm: FarmOut = { ...TEST_FARMS[0], name: "Location test farm", location, role: null };
  let farmResponses = 0;
  server.use(http.get("/api/auth/farms", () => {
    farmResponses++;
    return HttpResponse.json([farm]);
  }));
  const view = renderWithProviders(<LanguageProvider><FarmSelectPage /></LanguageProvider>);
  try {
    await view.waitForAuthIdle();
    const card = (await screen.findByText(farm.name)).closest("button");
    expect(card).not.toBeNull();
    expect(farmResponses).toBeGreaterThan(0);
    // Both optional-field encodings mean that the farm supplied no location.
    // The existing missing-value marker keeps that fact visible beside its role.
    expect(within(card!).getByText(`${expected} · Owner`)).toBeVisible();
  } finally {
    view.unmount(); view.queryClient.clear(); localStorage.removeItem("herdly.language");
  }
});

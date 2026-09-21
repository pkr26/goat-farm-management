/**
 * BucketsPage — fresh-domain mutation campaign kills (2026-09): the board
 * query must stay disabled without buckets.view.
 */

import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import BucketsPage from "./page";
import { settle } from "@/test/settle";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/buckets",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

describe("BucketsPage — campaign kills", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: false, permissions: ["dashboard.view"] }),
      ),
      http.get("/api/buckets", () => {
        return HttpResponse.json({ buckets: [] });
      }),
    );
  });

  it("does not fetch the board without buckets.view", async () => {
    let boardRequests = 0;
    server.use(
      http.get("/api/buckets", () => {
        boardRequests += 1;
        return HttpResponse.json({ buckets: [] });
      }),
    );
    renderWithProviders(<BucketsPage />);
    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
    await settle(50);
    expect(boardRequests).toBe(0);
  });
});

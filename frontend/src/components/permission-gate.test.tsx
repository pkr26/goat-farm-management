/**
 * PermissionGate: the shared module-page prologue — skeleton while the
 * permission set loads, retry affordance on a permissions failure, the
 * muted denial line when the code is missing, and children once granted.
 * The copy is asserted verbatim because page tests depend on it.
 */

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, delay, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { PermissionGate } from "@/components/permission-gate";
import { usePermissions } from "@/lib/use-permissions";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

/** Mirrors the page pattern: one observer, its result shared by gate+content. */
function Harness(props: Partial<Parameters<typeof PermissionGate>[0]> = {}) {
  const perms = usePermissions();
  return (
    <PermissionGate
      perms={perms}
      perm="animals.view"
      label="Animals"
      description="Your herd at a glance."
      cards={1}
      {...props}
    >
      <p>page content</p>
    </PermissionGate>
  );
}

describe("PermissionGate states", () => {
  it("shows the header-shaped skeleton while permissions load", async () => {
    server.use(
      http.get("/api/auth/permissions", async () => {
        await delay(50);
        return HttpResponse.json({ is_owner: true, permissions: ["animals.view"] });
      }),
    );
    renderWithProviders(<Harness />);
    // Header + content mirror the real page so navigation feels continuous.
    expect(screen.getByRole("heading", { level: 1, name: "Animals" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
  });

  it("renders children when the permission is granted", async () => {
    renderWithProviders(<Harness />);
    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
    expect(screen.queryByText(/You don't have access/)).not.toBeInTheDocument();
  });

  it("denies with the historical one-liner when the code is missing", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: false, permissions: ["tasks.view"] }),
      ),
    );
    renderWithProviders(<Harness />);
    await waitFor(() =>
      expect(screen.getByText("You don't have access to this page.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("page content")).not.toBeInTheDocument();
  });

  it("supports a translated denial line and a titled override", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: false, permissions: [] }),
      ),
    );
    const { rerender } = renderWithProviders(
      <Harness noAccessMessage="మీకు ఈ పేజీ యాక్సెస్ లేదు." />,
    );
    await waitFor(() =>
      expect(screen.getByText("మీకు ఈ పేజీ యాక్సెస్ లేదు.")).toBeInTheDocument(),
    );
    rerender(
      <Harness noAccessTitle="No animals access" noAccessDescription="Ask the farm owner." />,
    );
    await waitFor(() =>
      expect(screen.getByText("No animals access")).toBeInTheDocument(),
    );
    expect(screen.getByText("Ask the farm owner.")).toBeInTheDocument();
  });

  it("offers an in-place retry when the permissions call fails", async () => {
    let failures = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        failures += 1;
        return failures === 1
          ? HttpResponse.json({ detail: "boom" }, { status: 503 })
          : HttpResponse.json({ is_owner: true, permissions: ["animals.view"] });
      }),
    );
    renderWithProviders(<Harness />);
    await waitFor(() =>
      expect(
        screen.getByText("Could not load your permissions — refresh the page to try again."),
      ).toBeInTheDocument(),
    );
    screen.getByRole("button", { name: "Retry permissions" }).click();
    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
  });
});

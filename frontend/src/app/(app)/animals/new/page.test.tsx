/** /animals/new shim: redirects to /animals?new=1, which auto-opens the
 *  create-animal dialog. */

import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AnimalsNewRedirect from "./page";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/animals/new",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

describe("/animals/new redirect shim", () => {
  afterEach(() => {
    replaceMock.mockClear();
  });

  it("redirects once to /animals?new=1 when Strict Mode replays the effect", async () => {
    render(
      <StrictMode>
        <AnimalsNewRedirect />
      </StrictMode>,
    );

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/animals?new=1"),
    );
    expect(replaceMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the redirect to a single dispatch when the router instance changes across re-renders", async () => {
    // useRouter() hands back a fresh object on every render, so a re-render
    // re-runs the effect; the ref guard — not the dependency list — is what
    // stops the shim from dispatching a second /animals?new=1 transition.
    const { rerender } = render(<AnimalsNewRedirect />);

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/animals?new=1"),
    );

    rerender(<AnimalsNewRedirect />);
    rerender(<AnimalsNewRedirect />);

    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith("/animals?new=1");
    // The shim stays on its placeholder while the transition is in flight.
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});

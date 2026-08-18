/** /animals/new shim: redirects to /animals?new=1, which auto-opens the
 *  create-animal dialog. */

import { render, waitFor } from "@testing-library/react";
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
});

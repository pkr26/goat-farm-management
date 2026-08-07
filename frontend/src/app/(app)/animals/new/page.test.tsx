/** /animals/new shim: bounces to /animals?new=1, which auto-opens the
 *  create-animal dialog. */

import { render, waitFor } from "@testing-library/react";
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

  it("redirects to /animals?new=1", async () => {
    render(<AnimalsNewRedirect />);

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/animals?new=1"),
    );
  });
});

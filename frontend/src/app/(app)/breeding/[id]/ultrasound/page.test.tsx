/** /breeding/{id}/ultrasound shim (backend task action_url): bounces to
 *  /breeding?ultrasound_id={id}, which auto-opens the ultrasound dialog. */

import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import BreedingUltrasoundRedirect from "./page";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/breeding/7/ultrasound",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ id: "7" }),
}));

describe("/breeding/[id]/ultrasound redirect shim", () => {
  afterEach(() => {
    replaceMock.mockClear();
  });

  it("redirects to /breeding?ultrasound_id=<id>", async () => {
    render(<BreedingUltrasoundRedirect />);

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/breeding?ultrasound_id=7"),
    );
  });
});

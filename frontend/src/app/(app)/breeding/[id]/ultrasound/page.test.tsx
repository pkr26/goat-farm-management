/** /breeding/{id}/ultrasound shim (backend task action_url): redirects to
 *  /breeding?ultrasound_id={id}, which auto-opens the ultrasound dialog. */

import { render, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BreedingUltrasoundRedirect from "./page";

const { replaceMock, navState } = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  navState: { id: "7" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/breeding/7/ultrasound",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ id: navState.id }),
}));

describe("/breeding/[id]/ultrasound redirect shim", () => {
  beforeEach(() => {
    replaceMock.mockClear();
    navState.id = "7";
  });

  it("redirects once to /breeding?ultrasound_id=<id> under Strict Mode", async () => {
    render(
      <StrictMode>
        <BreedingUltrasoundRedirect />
      </StrictMode>,
    );

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/breeding?ultrasound_id=7"),
    );
    expect(replaceMock).toHaveBeenCalledTimes(1);
  });

  it("encodes a decoded route segment instead of letting it add query parameters", async () => {
    navState.id = "7&returnTo=/team";
    render(<BreedingUltrasoundRedirect />);

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith(
        "/breeding?ultrasound_id=7%26returnTo%3D%2Fteam",
      ),
    );
  });

  it("redirects again when Next reuses the page for a different record id", async () => {
    const { rerender } = render(<BreedingUltrasoundRedirect />);
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/breeding?ultrasound_id=7"),
    );

    replaceMock.mockClear();
    navState.id = "8";
    rerender(<BreedingUltrasoundRedirect />);

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/breeding?ultrasound_id=8"),
    );
  });
});

/** /health/new?… task-action shim: redirects to /health while preserving the
 * query string that hydrates the add-event dialog. */

import { render, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import HealthNewRedirect from "./page";

const { replaceMock, searchParams } = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  searchParams: { current: new URLSearchParams() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/health/new",
  useSearchParams: () => searchParams.current,
  useParams: () => ({}),
}));

describe("/health/new redirect shim", () => {
  afterEach(() => {
    replaceMock.mockClear();
    searchParams.current = new URLSearchParams();
  });

  it("redirects once to /health preserving the query string under Strict Mode", async () => {
    searchParams.current = new URLSearchParams("task_id=12&animal_id=3");
    render(
      <StrictMode>
        <HealthNewRedirect />
      </StrictMode>,
    );

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/health?task_id=12&animal_id=3"),
    );
    expect(replaceMock).toHaveBeenCalledTimes(1);
  });

  it("redirects to plain /health without a query string", async () => {
    render(<HealthNewRedirect />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/health"));
  });

  it("re-redirects when only the query string changes", async () => {
    searchParams.current = new URLSearchParams("task_id=12");
    const { rerender } = render(<HealthNewRedirect />);
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/health?task_id=12"),
    );

    searchParams.current = new URLSearchParams("task_id=99");
    rerender(<HealthNewRedirect />);

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/health?task_id=99"),
    );
  });
});

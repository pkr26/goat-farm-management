import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { LanguageProvider } from "@/lib/i18n";
import { createTestQueryClient } from "@/test/render";
import { HealthRoundProgress } from "./health-round-progress";

const { start, refetch, toastSuccess } = vi.hoisted(() => ({
  start: vi.fn(), refetch: vi.fn(), toastSuccess: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: { success: toastSuccess } }));
vi.mock("@/api/generated/endpoints", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useHealthRoundProgressApiHealthRoundsTaskIdGet: () => ({
    data: { status: 200, data: {
      task_id: 42, task_status: "PENDING", initialized: false,
      covered_targets: 0, total_targets: 2, excluded_targets: 0, remaining_units: 2,
    } },
    isPending: false, isError: false, refetch,
  }),
  useStartHealthRoundApiHealthRoundsTaskIdStartPost: () => ({ isPending: false, mutateAsync: start }),
  useAddHealthRoundTargetsApiHealthRoundsTaskIdTargetsPost: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useExcludeHealthRoundTargetsApiHealthRoundsTaskIdExclusionsPost: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));

beforeEach(() => {
  localStorage.setItem("herdly.language", "en");
  setAccessToken("round-worker", 7); setCurrentFarmId("3");
  start.mockReset(); refetch.mockReset().mockResolvedValue({}); toastSuccess.mockClear();
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((finish) => { resolve = finish; });
  return { promise, resolve };
}

function showRound(onCohortChange = vi.fn()) {
  return { onCohortChange, ...render(
    <QueryClientProvider client={createTestQueryClient()}><LanguageProvider>
      <HealthRoundProgress taskId={42} component="" onComponentChange={vi.fn()} onCohortChange={onCohortChange} />
    </LanguageProvider></QueryClientProvider>,
  ) };
}

describe("health round continuation ownership", () => {
  it("does not resume the closed round's UI when its deferred mutation succeeds", async () => {
    const mutation = deferred();
    start.mockReturnValue(mutation.promise);
    const user = userEvent.setup();
    const view = showRound();
    await user.click(screen.getByRole("button", { name: "Start herd round" }));
    expect(start).toHaveBeenCalledOnce();
    view.unmount();
    await act(async () => { mutation.resolve(); });

    expect(view.onCohortChange).not.toHaveBeenCalled();
    expect(refetch).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it.each(["unmount", "farm switch"])("suppresses the old round's success toast after %s during refetch", async (change) => {
    const refresh = deferred();
    start.mockResolvedValue({});
    refetch.mockReturnValue(refresh.promise);
    const user = userEvent.setup();
    const view = showRound();
    await user.click(screen.getByRole("button", { name: "Start herd round" }));
    await waitFor(() => expect(refetch).toHaveBeenCalledOnce());
    expect(view.onCohortChange).toHaveBeenCalledOnce();

    if (change === "unmount") view.unmount();
    else setCurrentFarmId("4");
    await act(async () => { refresh.resolve(); });

    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("announces success after refetch while the round still owns its view and farm", async () => {
    const refresh = deferred();
    start.mockResolvedValue({});
    refetch.mockReturnValue(refresh.promise);
    const user = userEvent.setup();
    const view = showRound();
    await user.click(screen.getByRole("button", { name: "Start herd round" }));
    await waitFor(() => expect(refetch).toHaveBeenCalledOnce());
    expect(toastSuccess).not.toHaveBeenCalled();
    await act(async () => { refresh.resolve(); });

    expect(view.onCohortChange).toHaveBeenCalledOnce();
    expect(toastSuccess).toHaveBeenCalledExactlyOnceWith("Round cohort updated");
  });
});

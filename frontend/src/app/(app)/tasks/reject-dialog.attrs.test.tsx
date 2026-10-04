/**
 * Reject-dialog textarea attributes: the skip/reject reason box carries maxLength
 * 255 (backend VARCHAR(255)) and a 3-row height — DOM attributes, not styling, so
 * they are pinned directly.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { TaskOut } from "@/api/generated/models";

import { farmToday } from "@/lib/format";
import { translate } from "@/lib/i18n";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import { default as TasksPage } from "./page";

const nav = vi.hoisted(() => ({
  router: { push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() },
}));
const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

const t = (key: Parameters<typeof translate>[1]) => translate("en", key);

const TEAM_PAYLOAD = {
  memberships: [],
  roles: [],
  permission_groups: [],
  permission_labels: {},
};

function makeTask(overrides: Partial<TaskOut>): TaskOut {
  return {
    id: 1,
    title: "Task",
    title_key: null,
    title_args: {},
    due_date: farmToday(),
    status: "PENDING",
    category: "OTHER",
    auto_generated: false,
    animal_id: null,
    purchase_batch_id: null,
    breeding_record_id: null,
    assigned_role_id: null,
    assigned_user_id: null,
    recur_days: null,
    recurring_series_id: null,
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
    skipped_by_id: null,
    skipped_at: null,
    skip_reason: null,
    rejected_by_id: null,
    rejected_at: null,
    created_at: "2026-01-01T00:00:00Z",
    created_by_id: null,
    action_url: null,
    ...overrides,
  };
}

function boardPayload() {
  return {
    today: [],
    overdue: [],
    upcoming: [],
    awaiting: [
      makeTask({
        id: 5,
        title: "Awaiting check",
        status: "DONE",
        needs_verification: true,
        completed_by_id: 999,
        completed_at: "2026-08-05T14:07:00",
      }),
    ],
    completed: [],
    today_total: 0,
    today_offset: 0,
    overdue_total: 0,
    overdue_offset: 0,
    upcoming_total: 0,
    upcoming_offset: 0,
    awaiting_total: 1,
    awaiting_offset: 0,
    active_limit: 50,
    completed_total: 0,
    completed_limit: 50,
    completed_offset: 0,
  };
}

function mobileCardList(): HTMLElement {
  const list = document.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

describe("reject dialog textarea attributes", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/tasks", () => HttpResponse.json(boardPayload())),
      http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)),
      permissionsHandler(["tasks.view", "tasks.create", "tasks.verify", "tasks.complete"]),
    );
  });

  it("caps the reason at 255 characters in a 3-row box", async () => {
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (0)" });
    await userEvent.click(screen.getByRole("tab", { name: /awaiting/i }));
    await within(mobileCardList()).findByText("Awaiting check");

    await userEvent.click(
      within(mobileCardList()).getByRole("button", { name: t("tasks.reject") }),
    );
    const dialog = await screen.findByRole("dialog");
    const reason = within(dialog).getByLabelText(t("tasks.reject.reason"));
    expect(reason).toHaveAttribute("maxlength", "255");
    expect(reason).toHaveAttribute("rows", "3");
    await userEvent.click(within(dialog).getByRole("button", { name: t("common.cancel") }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

/**
 * Team page — per-worker notification preferences (playbook ITEM 4.6): the
 * owner create-from-null flow (empty form → phone + two classes → PUT body
 → toast), the delegated manager's read-only view (Save disabled, controls
 * inert, hint shown), and a 422 round-trip that keeps the dialog open with
 * the server's sentence surfaced.
 */

import { configure, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import TeamPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/team",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const toastMock = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

beforeAll(() => {
  // Dialog round-trips can outrun the 1 s findBy*/waitFor default when this
  // file shares a loaded machine with the rest of the suite.
  configure({ asyncUtilTimeout: 3000 });
  // jsdom lacks the pointer-capture/scroll APIs Base UI controls rely on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

const ROLE_HELPER = {
  id: 11,
  code: null,
  name: "Helper",
  description: "general farm help",
  permissions: [],
  revision: 1,
  member_count: 1,
};

const MEMBER_SELF = {
  id: 1,
  user_id: TEST_USER.id,
  email: TEST_USER.email,
  name: TEST_USER.name,
  role_id: null,
  role_name: null,
  is_active: true,
  can_reset_password: false,
  reset_password_block_reason: "This account must use self-service password recovery.",
};

/** A plain manageable worker — the notifications affordance lives on rows
 * that are neither the viewer's own nor above their permission ceiling. */
const MEMBER_RAVI = {
  id: 2,
  user_id: 22,
  email: "ravi@example.com",
  name: "Ravi Kumar",
  role_id: ROLE_HELPER.id,
  role_name: ROLE_HELPER.name,
  is_active: true,
  can_reset_password: true,
  reset_password_block_reason: null,
};

const TEAM_PAYLOAD = {
  memberships: [MEMBER_SELF, MEMBER_RAVI],
  roles: [ROLE_HELPER],
  permission_groups: [{ group: "Team", codes: ["team.manage"] }],
  permission_labels: { "team.manage": "Manage team, roles & passwords" },
};

const PREFS_URL = "/api/team/workers/:membershipId/notifications";

/** A saved preference row the read-only view pre-fills from. */
const SAVED_PREFS = {
  membership_id: MEMBER_RAVI.id,
  phone: "+919876543210",
  daily_digest: true,
  screening_flags: false,
  kidding_watch: true,
  overdue_critical: false,
  feed_reorder: false,
  movement_restriction: false,
  verified: true,
};

function teamHandler() {
  return http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD));
}

function workerRow(email: string): HTMLElement {
  // Worker content also renders in the below-md card list (md:hidden) — scope
  // to the desktop table so duplicated text stays unambiguous.
  const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
  const row = within(table).getByText(email).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

async function renderLoaded() {
  renderWithProviders(<TeamPage />);
  expect((await screen.findAllByText(MEMBER_RAVI.email)).length).toBeGreaterThan(0);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "New role" })).toBeEnabled(),
  );
}

async function openNotificationsDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Notifications" }),
  );
  return screen.findByRole("dialog");
}

beforeEach(() => {
  vi.clearAllMocks();
  server.use(teamHandler());
});

describe("TeamPage notification preferences — owner", () => {
  it("creates prefs from an unset (null) GET: empty form, toggled classes, PUT body, toast", async () => {
    let putBody: Record<string, unknown> | null = null;
    let putMembershipId: string | null = null;
    server.use(
      http.get(PREFS_URL, () => HttpResponse.json(null, { status: 200 })),
      http.put(PREFS_URL, async ({ request, params }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        putMembershipId = String(params.membershipId);
        return HttpResponse.json({ ...SAVED_PREFS, phone: "9876543210" }, { status: 200 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    const dialog = await openNotificationsDialog(user);

    // GET answered null → nothing prefilled, the unset hint explains it.
    expect(
      within(dialog).getByText("No preferences set yet — every alert starts switched off."),
    ).toBeInTheDocument();
    expect((within(dialog).getByLabelText(/phone number/i) as HTMLInputElement).value).toBe("");
    for (const name of [
      "Daily digest",
      "Screening flags",
      "Kidding watch",
      "Overdue critical",
      "Feed reorder",
      "Movement restriction",
      "Number verified with the worker",
    ]) {
      expect(within(dialog).getByRole("checkbox", { name })).not.toBeChecked();
    }

    await user.type(within(dialog).getByLabelText(/phone number/i), "9876543210");
    await user.click(within(dialog).getByRole("checkbox", { name: "Daily digest" }));
    await user.click(within(dialog).getByRole("checkbox", { name: "Kidding watch" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putMembershipId).toBe(String(MEMBER_RAVI.id));
    // A save is a full snapshot: every class boolean travels, not a patch.
    expect(putBody).toEqual({
      phone: "9876543210",
      daily_digest: true,
      screening_flags: false,
      kidding_watch: true,
      overdue_critical: false,
      feed_reorder: false,
      movement_restriction: false,
      verified: false,
    });
    expect(toastMock.success).toHaveBeenCalledWith("Notification settings saved.");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("pre-fills the saved values when the GET returns a preference row", async () => {
    server.use(http.get(PREFS_URL, () => HttpResponse.json(SAVED_PREFS)));
    const user = userEvent.setup();
    await renderLoaded();

    const dialog = await openNotificationsDialog(user);

    expect(
      (within(dialog).getByLabelText(/phone number/i) as HTMLInputElement).value,
    ).toBe("+919876543210");
    expect(within(dialog).getByRole("checkbox", { name: "Daily digest" })).toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: "Kidding watch" })).toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: "Screening flags" })).not.toBeChecked();
    expect(
      within(dialog).getByRole("checkbox", { name: "Number verified with the worker" }),
    ).toBeChecked();
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeEnabled();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("TeamPage notification preferences — delegated manager", () => {
  beforeEach(() => {
    // is_owner false: PUT /notifications is owner-only, so the dialog must
    // present the prefs read-only instead of offering a doomed save.
    server.use(permissionsHandler(["team.manage"]));
  });

  it("shows the saved prefs read-only with Save disabled and a hint", async () => {
    let puts = 0;
    server.use(
      http.get(PREFS_URL, () => HttpResponse.json(SAVED_PREFS)),
      http.put(PREFS_URL, () => {
        puts += 1;
        return HttpResponse.json(SAVED_PREFS);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    const dialog = await openNotificationsDialog(user);

    expect(
      within(dialog).getByText("Only the farm owner can change notification settings."),
    ).toBeInTheDocument();
    // jest-dom's toBeDisabled() accounts for the disabled fieldset ancestor
    // (the input's own `disabled` property does not).
    expect(within(dialog).getByLabelText(/phone number/i)).toBeDisabled();
    // The manager still SEES the truth: saved values prefill the inert controls.
    expect(
      (within(dialog).getByLabelText(/phone number/i) as HTMLInputElement).value,
    ).toBe("+919876543210");
    for (const name of ["Daily digest", "Kidding watch", "Number verified with the worker"]) {
      expect(within(dialog).getByRole("checkbox", { name })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
      expect(within(dialog).getByRole("checkbox", { name })).toBeChecked();
    }
    const save = within(dialog).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(puts).toBe(0);
  });
});

describe("TeamPage notification preferences — save failure", () => {
  it("a 422 surfaces the server sentence inline, toasts, and keeps the dialog open", async () => {
    let puts = 0;
    server.use(
      http.get(PREFS_URL, () => HttpResponse.json(null)),
      http.put(PREFS_URL, () => {
        puts += 1;
        return HttpResponse.json(
          { detail: "phone number is not a valid recipient" },
          { status: 422 },
        );
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    const dialog = await openNotificationsDialog(user);
    await user.type(within(dialog).getByLabelText(/phone number/i), "9876543210");
    await user.click(within(dialog).getByRole("checkbox", { name: "Overdue critical" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "phone number is not a valid recipient",
    );
    expect(toastMock.error).toHaveBeenCalledWith("phone number is not a valid recipient");
    // The dialog survives so the owner can fix the number and retry.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Retry save" })).toBeInTheDocument();
    expect(puts).toBe(1);
  });

  it("blocks an off-pattern phone client-side without any request", async () => {
    let puts = 0;
    server.use(
      http.get(PREFS_URL, () => HttpResponse.json(null)),
      http.put(PREFS_URL, () => {
        puts += 1;
        return HttpResponse.json(SAVED_PREFS);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    const dialog = await openNotificationsDialog(user);
    await user.type(within(dialog).getByLabelText(/phone number/i), "12345");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Enter 10–19 digits, optionally starting with +.",
    );
    expect(puts).toBe(0);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

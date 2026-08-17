/**
 * Team page (extended, owner session): workers table (role select, status
 * badge, self guard), add-worker dialog (email/password/role validation +
 * payload mapping), reset-password dialog, deactivate/activate toggle, role
 * cards (preset/member-count delete guards, window.confirm), role create/edit
 * dialogs, error state and RBAC gating. The reduced-permission clamping of
 * the role dialog lives in page.test.tsx.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import TeamPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/team",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture/scroll APIs Radix Select relies on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

const ROLE_NIGHT_WATCH = {
  id: 10,
  code: null,
  name: "Night Watch",
  description: null,
  permissions: ["animals.view", "animals.create"],
  member_count: 1,
};

const ROLE_HELPER = {
  id: 11,
  code: null,
  name: "Helper",
  description: "general farm help",
  permissions: [],
  member_count: 1,
};

const ROLE_UNUSED = {
  id: 12,
  code: null,
  name: "Unused",
  description: null,
  permissions: ["animals.view"],
  member_count: 0,
};

const ROLE_PRESET = {
  id: 13,
  code: "MANAGER",
  name: "Manager",
  description: "preset manager role",
  permissions: ["team.manage"],
  member_count: 0,
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

const MEMBER_RAVI = {
  id: 2,
  user_id: 22,
  email: "ravi@example.com",
  name: "Ravi Kumar",
  role_id: 10,
  role_name: "Night Watch",
  is_active: true,
  can_reset_password: true,
  reset_password_block_reason: null,
};

const MEMBER_SITA = {
  id: 3,
  user_id: 23,
  email: "sita@example.com",
  name: null,
  role_id: 11,
  role_name: "Helper",
  is_active: false,
  can_reset_password: false,
  reset_password_block_reason: "Reactivate this membership before resetting the password.",
};

const TEAM_PAYLOAD = {
  memberships: [MEMBER_SELF, MEMBER_RAVI, MEMBER_SITA],
  roles: [ROLE_NIGHT_WATCH, ROLE_HELPER, ROLE_UNUSED, ROLE_PRESET],
  permission_groups: [
    { group: "Animals", codes: ["animals.view", "animals.create"] },
    { group: "Team", codes: ["team.manage"] },
  ],
  permission_labels: {
    "animals.view": "View animals",
    "animals.create": "Add animals",
    "team.manage": "Manage team, roles & passwords",
  },
};

function teamHandler(payload: Record<string, unknown> = TEAM_PAYLOAD) {
  return http.get("/api/team", () => HttpResponse.json(payload));
}

function workerRow(email: string): HTMLElement {
  const row = screen.getByText(email).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

async function renderLoaded() {
  renderWithProviders(<TeamPage />);
  expect(await screen.findByText(MEMBER_RAVI.email)).toBeInTheDocument();
}

describe("TeamPage workers table", () => {
  beforeEach(() => {
    server.use(teamHandler());
  });

  it("renders each worker with name, email, current role and status", async () => {
    await renderLoaded();

    const ravi = workerRow(MEMBER_RAVI.email);
    expect(within(ravi).getByText("Ravi Kumar")).toBeInTheDocument();
    // Role select present for reassignment (Radix SelectValue text is only
    // rendered client-side after hydration, so assert the combobox itself).
    expect(within(ravi).getByRole("combobox")).toBeEnabled();
    // The closed trigger shows the role label, not the raw role id.
    expect(within(ravi).getByRole("combobox")).toHaveTextContent("Night Watch");
    expect(within(ravi).getByText("Active")).toBeInTheDocument();

    const sita = workerRow(MEMBER_SITA.email);
    expect(within(sita).getByText("—")).toBeInTheDocument(); // null name
    expect(within(sita).getByText("Inactive")).toBeInTheDocument();
  });

  it("guards the self row and directs password changes to Account", async () => {
    await renderLoaded();

    const self = workerRow(TEST_USER.email);
    expect(within(self).getByText("Manage your password from Account.")).toBeInTheDocument();
    expect(within(self).queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();

    // Other rows get Deactivate (active) / Activate (inactive).
    expect(within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Deactivate" }))
      .toBeInTheDocument();
    expect(within(workerRow(MEMBER_SITA.email)).getByRole("button", { name: "Activate" }))
      .toBeInTheDocument();
  });

  it("shows the empty message when there are no memberships", async () => {
    server.use(teamHandler({ ...TEAM_PAYLOAD, memberships: [] }));
    renderWithProviders(<TeamPage />);

    expect(await screen.findByText(/No workers yet/)).toBeInTheDocument();
  });

  it("PUTs the desired inactive state and refetches the team", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    let toggleCalls = 0;
    let toggledId: string | null = null;
    let getCalls = 0;
    server.use(
      http.get("/api/team", () => {
        getCalls += 1;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.put("/api/team/workers/:membershipId/status", async ({ params, request }) => {
        toggleCalls += 1;
        toggledId = String(params.membershipId);
        expect(await request.json()).toEqual({ is_active: false });
        return HttpResponse.json({ ...MEMBER_RAVI, is_active: false });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const callsBefore = getCalls;

    await user.click(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Deactivate" }),
    );

    expect(confirmSpy).toHaveBeenCalledWith(
      "Deactivate Ravi Kumar? They will immediately lose farm access.",
    );
    await waitFor(() => expect(toggleCalls).toBe(1));
    expect(toggledId).toBe("2");
    await waitFor(() => expect(getCalls).toBeGreaterThan(callsBefore));
  });

  it("POSTs the new role when the row's role select changes", async () => {
    let roleBody: Record<string, unknown> | null = null;
    let roleMembership: string | null = null;
    server.use(
      teamHandler(),
      http.post("/api/team/workers/:membershipId/role", async ({ request, params }) => {
        roleMembership = String(params.membershipId);
        roleBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...MEMBER_RAVI, role_id: 11 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(within(workerRow(MEMBER_RAVI.email)).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /Helper/ }));

    await waitFor(() => expect(roleBody).not.toBeNull());
    expect(roleMembership).toBe("2");
    expect(roleBody).toEqual({ role_id: 11 });
  });

  it("announces a failed role change and retries the intended role", async () => {
    let calls = 0;
    server.use(
      teamHandler(),
      http.post("/api/team/workers/:membershipId/role", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "role assignment conflict" }, { status: 409 })
          : HttpResponse.json({ ...MEMBER_RAVI, role_id: 11 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);
    await user.click(within(row).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /Helper/ }));

    expect(await within(row).findByRole("alert")).toHaveTextContent("role assignment conflict");
    await user.click(within(row).getByRole("button", { name: "Retry role change" }));
    await waitFor(() => expect(calls).toBe(2));
  });

  it("requires confirmation before deactivating a worker", async () => {
    let calls = 0;
    vi.spyOn(window, "confirm").mockReturnValue(false);
    server.use(
      http.put("/api/team/workers/:membershipId/status", () => {
        calls += 1;
        return HttpResponse.json({ ...MEMBER_RAVI, is_active: false });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Deactivate" }),
    );

    expect(calls).toBe(0);
  });

  it("retries the same desired worker state instead of inverting it", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const bodies: unknown[] = [];
    server.use(
      teamHandler(),
      http.put("/api/team/workers/:membershipId/status", async ({ request }) => {
        bodies.push(await request.json());
        return bodies.length === 1
          ? HttpResponse.json({ detail: "response was interrupted" }, { status: 503 })
          : HttpResponse.json({ ...MEMBER_RAVI, is_active: false });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);
    await user.click(within(row).getByRole("button", { name: "Deactivate" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("response was interrupted");
    await user.click(within(row).getByRole("button", { name: "Retry deactivate" }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies).toEqual([{ is_active: false }, { is_active: false }]);
  });
});

describe("TeamPage add-worker dialog", () => {
  let postCalls: number;
  let postBody: Record<string, unknown> | null;

  beforeEach(() => {
    postCalls = 0;
    postBody = null;
    server.use(
      teamHandler(),
      http.post("/api/team/workers", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(MEMBER_RAVI, { status: 201 });
      }),
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add worker" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  async function pickRole(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Night Watch" }));
  }

  it("rejects an invalid email", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Email/), "not-an-email");
    await pickRole(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(await within(dialog).findByText("Enter a valid email address")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects an email above the API limit and accepts the exact boundary", async () => {
    const { user, dialog } = await openDialog();
    const email = within(dialog).getByLabelText(/Email/);
    const tooLongEmail = `${"a".repeat(243)}@example.com`;
    const maxEmail = `${"a".repeat(242)}@example.com`;

    // fireEvent intentionally bypasses the browser's maxLength guard so the
    // schema remains the authority for pasted/programmatic values.
    fireEvent.change(email, { target: { value: tooLongEmail } });
    await pickRole(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(
      await within(dialog).findByText("Email must be at most 254 characters"),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);

    fireEvent.change(email, { target: { value: maxEmail } });
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ email: maxEmail });
  });

  it("rejects a password shorter than 12 characters", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "short12");
    await pickRole(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(
      await within(dialog).findByText("Password must be at least 12 characters"),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("requires a role", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(await within(dialog).findByText("Pick a role")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("POSTs a null name and the required password", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Email/), "  new@example.com ");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await pickRole(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toEqual({
      email: "new@example.com",
      name: null,
      role_id: 10,
      password: "newworker123",
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("POSTs trimmed name and the given password", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/^Name/), "  Ravi Kumar  ");
    await user.type(within(dialog).getByLabelText(/Email/), "ravi@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "supersecret12");
    await pickRole(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toEqual({
      email: "ravi@example.com",
      name: "Ravi Kumar",
      role_id: 10,
      password: "supersecret12",
    });
  });

  it("shows the server detail inline on a 400", async () => {
    server.use(
      http.post("/api/team/workers", () => {
        postCalls += 1;
        return HttpResponse.json({ detail: "email already on this farm" }, { status: 400 });
      }),
    );
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Email/), "ravi@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await pickRole(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("email already on this farm");
    expect(within(dialog).getByRole("button", { name: "Retry add worker" })).toBeEnabled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("focuses the email field and associates validation errors", async () => {
    const { user, dialog } = await openDialog();
    const email = within(dialog).getByLabelText(/Email/);
    expect(email).toHaveFocus();

    await user.type(email, "invalid");
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));
    const error = await within(dialog).findByText("Enter a valid email address");
    expect(error).toHaveAttribute("role", "alert");
    expect(email).toHaveAccessibleDescription("Enter a valid email address");
  });

  it("single-flights a double-click on Add worker", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await pickRole(user, dialog);

    await user.dblClick(within(dialog).getByRole("button", { name: "Add worker" }));
    await waitFor(() => expect(postCalls).toBe(1));
  });
});

describe("TeamPage reset-password dialog", () => {
  let resetCalls: number;
  let resetBody: Record<string, unknown> | null;
  let resetMembership: string | null;

  beforeEach(() => {
    resetCalls = 0;
    resetBody = null;
    resetMembership = null;
    server.use(
      teamHandler(),
      http.post(
        "/api/team/workers/:membershipId/reset-password",
        async ({ request, params }) => {
          resetCalls += 1;
          resetMembership = String(params.membershipId);
          resetBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ ok: true });
        },
      ),
    );
  });

  async function openReset(email = MEMBER_RAVI.email) {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(workerRow(email)).getByRole("button", { name: "Reset password" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("opens titled with the worker's name", async () => {
    const { dialog } = await openReset();
    expect(within(dialog).getByText("Reset password — Ravi Kumar")).toBeInTheDocument();
  });

  it("falls back to the email when the worker has no name", async () => {
    server.use(
      teamHandler({
        ...TEAM_PAYLOAD,
        memberships: [
          MEMBER_SELF,
          MEMBER_RAVI,
          {
            ...MEMBER_SITA,
            is_active: true,
            can_reset_password: true,
            reset_password_block_reason: null,
          },
        ],
      }),
    );
    const { dialog } = await openReset(MEMBER_SITA.email);
    expect(
      within(dialog).getByText(`Reset password — ${MEMBER_SITA.email}`),
    ).toBeInTheDocument();
  });

  it("rejects a password shorter than 12 characters", async () => {
    const { user, dialog } = await openReset();

    await user.type(within(dialog).getByLabelText(/New password/), "short12");
    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));

    expect(
      await within(dialog).findByText("Password must be at least 12 characters"),
    ).toBeInTheDocument();
    expect(resetCalls).toBe(0);
  });

  it("POSTs the new password for the right membership and closes", async () => {
    const { user, dialog } = await openReset();

    await user.type(within(dialog).getByLabelText(/New password/), "brandnewpass");
    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));

    await waitFor(() => expect(resetCalls).toBe(1));
    expect(resetMembership).toBe("2");
    expect(resetBody).toEqual({ password: "brandnewpass" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows the server detail inline on a 400", async () => {
    server.use(
      http.post("/api/team/workers/:membershipId/reset-password", () => {
        resetCalls += 1;
        return HttpResponse.json({ detail: "cannot reset owner password" }, { status: 400 });
      }),
    );
    const { user, dialog } = await openReset();

    await user.type(within(dialog).getByLabelText(/New password/), "brandnewpass");
    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "cannot reset owner password",
    );
    expect(within(dialog).getByRole("button", { name: "Retry password reset" })).toBeEnabled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("disables an ineligible reset upfront and explains the backend policy", async () => {
    const reason = "This account must use self-service password recovery.";
    server.use(
      teamHandler({
        ...TEAM_PAYLOAD,
        memberships: [
          MEMBER_SELF,
          { ...MEMBER_RAVI, can_reset_password: false, reset_password_block_reason: reason },
        ],
      }),
    );
    await renderLoaded();

    const row = workerRow(MEMBER_RAVI.email);
    const reset = within(row).getByRole("button", { name: "Reset password" });
    expect(reset).toBeDisabled();
    expect(reset).toHaveAccessibleDescription(reason);
    expect(within(row).getByText(reason)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("TeamPage role cards", () => {
  beforeEach(() => {
    server.use(teamHandler());
  });

  function cardOf(name: string): HTMLElement {
    // Role card root: the rounded div wrapping the role name. The workers
    // table card is also rounded-xl (and role names show in its row select
    // triggers), so identify role cards by their Edit button.
    const el = screen
      .getAllByText(name)
      .map((n) => n.closest("div.rounded-xl"))
      .find(
        (n): n is HTMLElement =>
          n instanceof HTMLElement &&
          within(n).queryByRole("button", { name: "Edit" }) !== null,
      );
    expect(el).toBeDefined();
    return el as HTMLElement;
  }

  it("renders role cards with description, member count and permission badges", async () => {
    await renderLoaded();

    const helper = cardOf("Helper");
    expect(within(helper).getByText("general farm help")).toBeInTheDocument();
    expect(within(helper).getByText("1 member")).toBeInTheDocument();
    expect(within(helper).getByText("No permissions.")).toBeInTheDocument();

    const nightWatch = cardOf("Night Watch");
    expect(within(nightWatch).getByText("—")).toBeInTheDocument(); // null description
    expect(within(nightWatch).getByText("View animals")).toBeInTheDocument();
    expect(within(nightWatch).getByText("Add animals")).toBeInTheDocument();
  });

  it("marks preset roles and pluralizes the member count", async () => {
    server.use(
      teamHandler({
        ...TEAM_PAYLOAD,
        roles: [{ ...ROLE_PRESET, member_count: 3 }],
      }),
    );
    renderWithProviders(<TeamPage />);
    expect(await screen.findByText("Manager")).toBeInTheDocument();

    const preset = cardOf("Manager");
    expect(within(preset).getByText("preset")).toBeInTheDocument();
    expect(within(preset).getByText("3 members")).toBeInTheDocument();
  });

  it("disables Delete for preset roles and roles with workers", async () => {
    await renderLoaded();

    expect(within(cardOf("Manager")).getByRole("button", { name: "Delete" })).toBeDisabled();
    expect(within(cardOf("Night Watch")).getByRole("button", { name: "Delete" })).toBeDisabled();
    expect(within(cardOf("Unused")).getByRole("button", { name: "Delete" })).toBeEnabled();
  });

  it("does nothing when the delete confirmation is cancelled", async () => {
    let deleteCalls = 0;
    server.use(
      http.delete("/api/team/roles/:roleId", () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(within(cardOf("Unused")).getByRole("button", { name: "Delete" }));
    expect(window.confirm).toHaveBeenCalledWith('Delete role "Unused"?');
    expect(deleteCalls).toBe(0);
  });

  it("DELETEs the role after confirmation and refetches the team", async () => {
    let deleteCalls = 0;
    let deletedId: string | null = null;
    let getCalls = 0;
    server.use(
      http.get("/api/team", () => {
        getCalls += 1;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.delete("/api/team/roles/:roleId", ({ params }) => {
        deleteCalls += 1;
        deletedId = String(params.roleId);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    await renderLoaded();
    const callsBefore = getCalls;

    await user.click(within(cardOf("Unused")).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteCalls).toBe(1));
    expect(deletedId).toBe("12");
    await waitFor(() => expect(getCalls).toBeGreaterThan(callsBefore));
  });

  it("announces a failed role deletion and offers a confirmed retry", async () => {
    let calls = 0;
    vi.spyOn(window, "confirm").mockReturnValue(true);
    server.use(
      http.delete("/api/team/roles/:roleId", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "role changed concurrently" }, { status: 409 })
          : new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const card = cardOf("Unused");
    await user.click(within(card).getByRole("button", { name: "Delete" }));

    expect(await within(card).findByRole("alert")).toHaveTextContent("role changed concurrently");
    await user.click(within(card).getByRole("button", { name: "Retry delete role" }));
    await waitFor(() => expect(calls).toBe(2));
  });
});

describe("TeamPage role create/edit dialogs (owner holds all permissions)", () => {
  let putBody: Record<string, unknown> | null;
  let putRoleId: string | null;
  let postBody: Record<string, unknown> | null;

  beforeEach(() => {
    putBody = null;
    putRoleId = null;
    postBody = null;
    server.use(
      teamHandler(),
      http.post("/api/team/roles", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...ROLE_UNUSED, id: 14 }, { status: 201 });
      }),
      http.put("/api/team/roles/:roleId", async ({ request, params }) => {
        putRoleId = String(params.roleId);
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(ROLE_NIGHT_WATCH);
      }),
    );
  });

  it("requires a role name", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "New role" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    expect(await within(dialog).findByText("Name is required")).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("creates a role with every held permission group rendered", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "New role" }));
    const dialog = await screen.findByRole("dialog");

    // Owner: all groups from the payload are grantable and visible.
    expect(within(dialog).getByText("Animals")).toBeInTheDocument();
    expect(within(dialog).getByText("Team")).toBeInTheDocument();
    expect(within(dialog).getAllByRole("checkbox")).toHaveLength(3);

    await user.type(within(dialog).getByLabelText(/Role name/), "Watchman");
    // Tick "View animals" only.
    await user.click(within(dialog).getAllByRole("checkbox")[0]);
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toEqual({ name: "Watchman", description: null, permissions: ["animals.view"] });
  });

  it("selecting an action automatically selects its required view permission", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New role" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Role name/), "Animal entry");

    await user.click(within(dialog).getByRole("checkbox", { name: "Add animals" }));
    expect(within(dialog).getByRole("checkbox", { name: "View animals" })).toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      name: "Animal entry",
      permissions: ["animals.create", "animals.view"],
    });
  });

  it("edits a role: prefilled name, prechecked permissions, PUT on save", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    // Ravi's row trigger also renders "Night Watch" now (label, not raw id) —
    // pick the role card, identified by its Edit button.
    const card = screen
      .getAllByText("Night Watch")
      .map((el) => el.closest("div.rounded-xl") as HTMLElement | null)
      .find((el) => el && within(el).queryByRole("button", { name: "Edit" })) as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByText("Edit role: Night Watch")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Role name/)).toHaveValue("Night Watch");

    const checkboxes = within(dialog).getAllByRole("checkbox");
    expect(checkboxes[0]).toBeChecked(); // animals.view
    expect(checkboxes[1]).toBeChecked(); // animals.create
    expect(checkboxes[2]).not.toBeChecked(); // team.manage

    // Untick "Add animals" and save.
    await user.click(checkboxes[1]);
    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putRoleId).toBe("10");
    expect(putBody).toMatchObject({ name: "Night Watch", permissions: ["animals.view"] });
  });
});

describe("TeamPage RBAC and errors", () => {
  it("denies access without team.manage and never calls the endpoint", async () => {
    let calls = 0;
    server.use(
      permissionsHandler(["animals.view"]),
      http.get("/api/team", () => {
        calls += 1;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
    );
    renderWithProviders(<TeamPage />);

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    expect(calls).toBe(0);
  });

  it("shows the error detail when the team GET fails", async () => {
    server.use(
      http.get("/api/team", () =>
        HttpResponse.json({ detail: "team unavailable" }, { status: 500 }),
      ),
    );
    renderWithProviders(<TeamPage />);
    expect(await screen.findByText("team unavailable")).toBeInTheDocument();
  });

  it("announces a team query failure and retries it", async () => {
    let fail = true;
    let calls = 0;
    server.use(
      http.get("/api/team", () => {
        calls += 1;
        return fail
          ? HttpResponse.json({ detail: "team temporarily unavailable" }, { status: 503 })
          : HttpResponse.json(TEAM_PAYLOAD);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<TeamPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("team temporarily unavailable");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry team" }));
    expect(await screen.findByText(MEMBER_RAVI.email)).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  // REGRESSION — worker creation is owner-only server-side
  // (team.py _prepare_worker_create), so a delegated team.manage holder could
  // fill in a colleague's name, email and a plaintext password and only then
  // be told 403.
  it("hides Add worker from a delegated team.manage holder and explains why", async () => {
    server.use(permissionsHandler(["team.manage"]), teamHandler());
    await renderLoaded();

    expect(screen.queryByRole("button", { name: "Add worker" })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Only the farm owner can create worker accounts\./),
    ).toBeInTheDocument();
  });

  it("never exposes password reset to a non-owner even if a forged payload allows it", async () => {
    server.use(permissionsHandler(["team.manage"]), teamHandler());
    await renderLoaded();

    const row = workerRow(MEMBER_RAVI.email);
    expect(within(row).queryByRole("button", { name: "Reset password" }))
      .not.toBeInTheDocument();
    expect(within(row).getByRole("combobox")).toBeDisabled();
    expect(
      within(row).getByText(/current role stays within your own permissions/i),
    ).toBeInTheDocument();
  });
});

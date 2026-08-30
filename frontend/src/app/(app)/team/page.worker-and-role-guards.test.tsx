/**
 * Team page branch coverage: role-select labelling and its "No role"
 * placeholder, same-tick row/card claims, retried intent, dialog validation
 * wiring (aria-invalid + inline messages), the permission matrix's
 * dependency/ceiling annotations, and the guards that keep a dialog open — or
 * drop a stale editor — when events race the team snapshot. The end-to-end
 * flows live in page.extended.test.tsx and the reduced-permission clamping of
 * the role dialog in page.test.tsx.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { getTeamPageApiTeamGetQueryKey } from "@/api/generated/endpoints";
import { ALL_PERMISSIONS, permissionsHandler, server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import TeamPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/team",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture/scroll APIs the Select popup relies on.
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

const ROLE_KEEPER = {
  id: 10,
  code: null,
  name: "Night keeper",
  description: null,
  permissions: ["animals.view", "animals.create"],
  revision: 4,
  member_count: 1,
};

const ROLE_HELPER = {
  id: 11,
  code: null,
  name: "Helper",
  description: "general farm help",
  permissions: [],
  revision: 1,
  member_count: 1,
};

const ROLE_SPARE = {
  id: 12,
  code: null,
  name: "Spare",
  description: null,
  permissions: [],
  revision: 2,
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
  role_id: ROLE_KEEPER.id,
  role_name: ROLE_KEEPER.name,
  is_active: true,
  can_reset_password: true,
  reset_password_block_reason: null,
};

/** Name-less on purpose: the email is the label fallback everywhere. */
const MEMBER_SITA = {
  id: 3,
  user_id: 23,
  email: "sita@example.com",
  name: null,
  role_id: ROLE_HELPER.id,
  role_name: ROLE_HELPER.name,
  is_active: false,
  can_reset_password: true,
  reset_password_block_reason: null,
};

/** Enrolled but not yet given a role. */
const MEMBER_PRIYA = {
  id: 4,
  user_id: 24,
  email: "priya@example.com",
  name: "Priya Nair",
  role_id: null,
  role_name: null,
  is_active: true,
  can_reset_password: true,
  reset_password_block_reason: null,
};

const TEAM_PAYLOAD = {
  memberships: [MEMBER_SELF, MEMBER_RAVI, MEMBER_SITA, MEMBER_PRIYA],
  roles: [ROLE_KEEPER, ROLE_HELPER, ROLE_SPARE],
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

/** Role card root, identified by the Edit button only role cards carry. */
function roleCard(name: string): HTMLElement {
  const card = screen
    .getAllByText(name)
    .map((element) => element.closest("div.rounded-xl"))
    .find(
      (element): element is HTMLElement =>
        element instanceof HTMLElement &&
        within(element).queryByRole("button", { name: "Edit" }) !== null,
    );
  expect(card).toBeDefined();
  return card as HTMLElement;
}

/** One permission line of the matrix: checkbox plus its annotations. */
function permissionRow(dialog: HTMLElement, label: string): HTMLElement {
  const row = within(dialog).getByRole("checkbox", { name: label }).closest("div[data-disabled]");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

async function renderLoaded() {
  const rendered = renderWithProviders(<TeamPage />);
  expect(await screen.findByText(MEMBER_RAVI.email)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "New role" })).toBeEnabled());
  return rendered;
}

describe("TeamPage worker row branches", () => {
  beforeEach(() => {
    server.use(teamHandler());
  });

  it("labels every role select with the worker it belongs to", async () => {
    await renderLoaded();

    expect(
      within(workerRow(MEMBER_RAVI.email)).getByRole("combobox", { name: "Role for Ravi Kumar" }),
    ).toHaveTextContent(ROLE_KEEPER.name);
    // No name on file: the email carries the label instead of "null".
    expect(
      within(workerRow(MEMBER_SITA.email)).getByRole("combobox", {
        name: `Role for ${MEMBER_SITA.email}`,
      }),
    ).toHaveTextContent(ROLE_HELPER.name);
    // An unassigned worker reads as "No role", never as a raw value.
    expect(
      within(workerRow(MEMBER_PRIYA.email)).getByRole("combobox", { name: "Role for Priya Nair" }),
    ).toHaveTextContent("No role");
  });

  it("offers the disabled No role placeholder only to a worker without a role", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(within(workerRow(MEMBER_PRIYA.email)).getByRole("combobox"));
    const placeholder = await screen.findByRole("option", { name: "No role" });
    expect(placeholder).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("option", { name: ROLE_HELPER.name })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("option", { name: "No role" })).not.toBeInTheDocument(),
    );

    // A worker who already holds a role is never offered the placeholder.
    await user.click(within(workerRow(MEMBER_RAVI.email)).getByRole("combobox"));
    expect(await screen.findByRole("option", { name: ROLE_HELPER.name })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "No role" })).not.toBeInTheDocument();
  });

  it("claims every control in the row in the same tick a status change starts", async () => {
    server.use(
      http.put("/api/team/workers/:membershipId/status", () =>
        HttpResponse.json({ ...MEMBER_RAVI, is_active: false }),
      ),
    );
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);
    const deactivate = within(row).getByRole("button", { name: "Deactivate" });
    const reset = within(row).getByRole("button", { name: "Reset password" });

    // Mutation pending state only reaches React a microtask later, so the
    // row's own settling flag is what locks the paint on this very tick. The
    // confirmation click is what claims the row now (destruction is dialog-gated).
    fireEvent.click(deactivate);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Deactivate worker" }));

    expect(deactivate).toBeDisabled();
    expect(reset).toBeDisabled();
    expect(within(row).getByRole("combobox")).toBeDisabled();
    await waitFor(() => expect(deactivate).toBeEnabled());
    expect(reset).toBeEnabled();
  });

  it("claims every control in the row in the same tick a role retry starts", async () => {
    let calls = 0;
    server.use(
      http.post("/api/team/workers/:membershipId/role", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "role assignment conflict" }, { status: 409 })
          : HttpResponse.json({ ...MEMBER_RAVI, role_id: ROLE_HELPER.id });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);
    await user.click(within(row).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: ROLE_HELPER.name }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("role assignment conflict");
    const deactivate = within(row).getByRole("button", { name: "Deactivate" });

    fireEvent.click(within(row).getByRole("button", { name: "Retry role change" }));

    expect(deactivate).toBeDisabled();
    expect(within(row).getByRole("combobox")).toBeDisabled();
    await waitFor(() => expect(calls).toBe(2));
  });

  it("does not ask for confirmation again when a failed deactivation is retried", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.put("/api/team/workers/:membershipId/status", async ({ request }) => {
        bodies.push(await request.json());
        return bodies.length === 1
          ? HttpResponse.json({ detail: "status service unavailable" }, { status: 503 })
          : HttpResponse.json({ ...MEMBER_RAVI, is_active: false });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);

    await user.click(within(row).getByRole("button", { name: "Deactivate" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Deactivate worker" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("status service unavailable");
    await user.click(within(row).getByRole("button", { name: "Retry deactivate" }));

    await waitFor(() => expect(bodies).toHaveLength(2));
    // The intent was already confirmed once; the retry must not re-prompt
    // (the confirmation dialog never reopens for a retry).
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("retries the recorded status intent even after the snapshot flips the worker", async () => {
    const bodies: unknown[] = [];
    const activeSita = { ...MEMBER_SITA, is_active: true };
    let payload: Record<string, unknown> = TEAM_PAYLOAD;
    server.use(
      http.get("/api/team", () => HttpResponse.json(payload)),
      http.put("/api/team/workers/3/status", async ({ request }) => {
        bodies.push(await request.json());
        return bodies.length === 1
          ? HttpResponse.json({ detail: "status service unavailable" }, { status: 503 })
          : HttpResponse.json(activeSita);
      }),
    );
    const user = userEvent.setup();
    const { queryClient } = await renderLoaded();

    await user.click(
      within(workerRow(MEMBER_SITA.email)).getByRole("button", { name: "Activate" }),
    );
    expect(await within(workerRow(MEMBER_SITA.email)).findByRole("alert")).toHaveTextContent(
      "status service unavailable",
    );

    // Another admin activates her while the failure is still on screen.
    payload = {
      ...TEAM_PAYLOAD,
      memberships: [MEMBER_SELF, MEMBER_RAVI, activeSita, MEMBER_PRIYA],
    };
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: getTeamPageApiTeamGetQueryKey() });
    });
    await waitFor(() =>
      expect(within(workerRow(MEMBER_SITA.email)).getByRole("button", { name: "Deactivate" }))
        .toBeEnabled(),
    );

    await user.click(
      within(workerRow(MEMBER_SITA.email)).getByRole("button", { name: "Retry deactivate" }),
    );

    // The retry replays the intent that failed, never the inverse of a
    // status this row never asked to change.
    await waitFor(() => expect(bodies).toEqual([{ is_active: true }, { is_active: true }]));
  });

  it("never renders a block reason for a worker whose password can be reset", async () => {
    const stale = "Reactivate this membership before resetting the password.";
    server.use(
      teamHandler({
        ...TEAM_PAYLOAD,
        memberships: [
          MEMBER_SELF,
          { ...MEMBER_RAVI, can_reset_password: true, reset_password_block_reason: stale },
        ],
      }),
    );
    await renderLoaded();

    const row = workerRow(MEMBER_RAVI.email);
    const reset = within(row).getByRole("button", { name: "Reset password" });
    expect(reset).toBeEnabled();
    expect(reset).not.toHaveAttribute("aria-describedby");
    expect(within(row).queryByText(stale)).not.toBeInTheDocument();
    expect(row.querySelector(`#reset-password-reason-${MEMBER_RAVI.id}`)).toBeNull();
  });

  it("keeps password reset out of the signed-in owner's own row", async () => {
    await renderLoaded();

    const self = workerRow(TEST_USER.email);
    expect(within(self).queryByRole("button", { name: "Reset password" })).not.toBeInTheDocument();
    expect(
      within(self).queryByText(MEMBER_SELF.reset_password_block_reason),
    ).not.toBeInTheDocument();
    expect(within(self).getByText("Manage your password from Account.")).toBeInTheDocument();
    // A worker row still offers it to the owner.
    expect(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Reset password" }),
    ).toBeInTheDocument();
  });

  it("keeps password reset out of a delegated manager's reach for a worker they can manage", async () => {
    server.use(
      permissionsHandler(["team.manage"]),
      teamHandler({
        ...TEAM_PAYLOAD,
        memberships: [MEMBER_SELF, { ...MEMBER_SITA, is_active: true }],
        roles: [ROLE_HELPER],
      }),
    );
    renderWithProviders(<TeamPage />);
    expect(await screen.findByText(MEMBER_SITA.email)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "New role" })).toBeEnabled());

    // The Helper role stays inside the delegate's ceiling, so the row is
    // manageable — password reset is still owner-only.
    const row = workerRow(MEMBER_SITA.email);
    expect(within(row).getByRole("button", { name: "Deactivate" })).toBeEnabled();
    expect(within(row).queryByRole("button", { name: "Reset password" })).not.toBeInTheDocument();
  });

  it("protects only the workers whose current role exceeds a delegated ceiling", async () => {
    const keeper = { ...MEMBER_RAVI, role_id: ROLE_KEEPER.id };
    server.use(
      permissionsHandler(["team.manage"]),
      teamHandler({
        ...TEAM_PAYLOAD,
        memberships: [MEMBER_SELF, { ...MEMBER_SITA, is_active: true }, keeper, MEMBER_PRIYA],
        // The in-ceiling role is listed first: a row must consult its own
        // role, not whichever role happens to head the list.
        roles: [ROLE_HELPER, ROLE_KEEPER],
      }),
    );
    renderWithProviders(<TeamPage />);
    expect(await screen.findByText(MEMBER_RAVI.email)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "New role" })).toBeEnabled());
    const guard = "You can only manage workers whose current role stays within your own permissions.";

    const protectedRow = workerRow(MEMBER_RAVI.email);
    expect(within(protectedRow).getByText(guard)).toBeInTheDocument();
    expect(within(protectedRow).queryByRole("button", { name: "Deactivate" }))
      .not.toBeInTheDocument();
    expect(within(protectedRow).getByRole("combobox")).toBeDisabled();

    const helperRow = workerRow(MEMBER_SITA.email);
    expect(within(helperRow).queryByText(guard)).not.toBeInTheDocument();
    expect(within(helperRow).getByRole("button", { name: "Deactivate" })).toBeEnabled();

    // No role at all means there is nothing to be protected from.
    const rolelessRow = workerRow(MEMBER_PRIYA.email);
    expect(within(rolelessRow).queryByText(guard)).not.toBeInTheDocument();
    expect(within(rolelessRow).getByRole("button", { name: "Deactivate" })).toBeEnabled();
  });
});

describe("TeamPage add-worker dialog branches", () => {
  let postCalls: number;

  beforeEach(() => {
    postCalls = 0;
    server.use(
      teamHandler(),
      http.post("/api/team/workers", () => {
        postCalls += 1;
        return HttpResponse.json(MEMBER_RAVI, { status: 201 });
      }),
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    const rendered = await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add worker" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog, ...rendered };
  }

  it("marks nothing invalid before the first submit", async () => {
    const { dialog } = await openDialog();

    for (const field of [/^Name/, /Email/, /Password/]) {
      expect(within(dialog).getByLabelText(field)).not.toHaveAttribute("aria-invalid");
    }
    expect(within(dialog).getByRole("combobox")).not.toHaveAttribute("aria-invalid");
    expect(within(dialog).queryAllByRole("alert")).toHaveLength(0);
  });

  it("flags every rejected field with aria-invalid and an inline message", async () => {
    const { user, dialog } = await openDialog();
    const name = within(dialog).getByLabelText(/^Name/);
    const email = within(dialog).getByLabelText(/Email/);
    const password = within(dialog).getByLabelText(/Password/);

    // fireEvent bypasses the browser maxLength guard: the schema stays the
    // authority for pasted values.
    fireEvent.change(name, { target: { value: "n".repeat(121) } });
    await user.type(email, "not-an-email");
    await user.type(password, "short12");
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(await within(dialog).findByText("Enter a valid email address")).toBeInTheDocument();
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription(/120 characters/);
    expect(email).toHaveAttribute("aria-invalid", "true");
    expect(email).toHaveAccessibleDescription("Enter a valid email address");
    expect(password).toHaveAttribute("aria-invalid", "true");
    expect(password).toHaveAccessibleDescription("Password must be at least 12 characters");
    const role = within(dialog).getByRole("combobox");
    expect(role).toHaveAttribute("aria-invalid", "true");
    expect(role).toHaveAccessibleDescription("Pick a role");
    expect(postCalls).toBe(0);
  });

  it("clears the role error as soon as a role is picked", async () => {
    const { user, dialog } = await openDialog();

    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));
    expect(await within(dialog).findByText("Pick a role")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: ROLE_KEEPER.name }));

    await waitFor(() => expect(within(dialog).queryByText("Pick a role")).not.toBeInTheDocument());
    expect(within(dialog).getByRole("combobox")).not.toHaveAttribute("aria-invalid");
  });

  it("keeps the dialog open when a dismissal lands in the same tick as the submit", async () => {
    let releaseCreate!: () => void;
    const createGate = new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    server.use(
      http.post("/api/team/workers", async () => {
        postCalls += 1;
        await createGate;
        return HttpResponse.json(MEMBER_RAVI, { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: ROLE_KEEPER.name }));
    const submit = within(dialog).getByRole("button", { name: "Add worker" });

    // The submission is announced on the same tick it starts; a dismissal
    // arriving before the request settles must be refused.
    fireEvent.click(submit);
    expect(submit).toHaveTextContent("Adding…");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      releaseCreate();
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(postCalls).toBe(1);
  });

  it("clears a failed create error when the dialog is dismissed and reopened", async () => {
    server.use(
      http.post("/api/team/workers", () =>
        HttpResponse.json({ detail: "email already on this farm" }, { status: 400 }),
      ),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Email/), "ravi@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: ROLE_KEEPER.name }));
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "email already on this farm",
    );

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Add worker" }));

    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).queryAllByRole("alert")).toHaveLength(0);
    expect(within(reopened).getByRole("button", { name: "Add worker" })).toBeInTheDocument();
  });
});

describe("TeamPage reset-password dialog branches", () => {
  beforeEach(() => {
    server.use(teamHandler());
  });

  async function openReset() {
    const user = userEvent.setup();
    const rendered = await renderLoaded();
    await user.click(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Reset password" }),
    );
    const dialog = await screen.findByRole("dialog");
    return { user, dialog, ...rendered };
  }

  it("marks the password invalid only once a submit is rejected", async () => {
    const { user, dialog } = await openReset();
    const password = within(dialog).getByLabelText(/New password/);
    expect(password).not.toHaveAttribute("aria-invalid");
    expect(within(dialog).queryAllByRole("alert")).toHaveLength(0);

    await user.type(password, "short12");
    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));

    expect(
      await within(dialog).findByText("Password must be at least 12 characters"),
    ).toBeInTheDocument();
    expect(password).toHaveAttribute("aria-invalid", "true");
    expect(password).toHaveAccessibleDescription("Password must be at least 12 characters");
  });

  it("keeps the dialog open when a dismissal lands in the same tick as the submit", async () => {
    let resetCalls = 0;
    let releaseReset!: () => void;
    const resetGate = new Promise<void>((resolve) => {
      releaseReset = resolve;
    });
    server.use(
      http.post("/api/team/workers/:membershipId/reset-password", async () => {
        resetCalls += 1;
        await resetGate;
        return HttpResponse.json({ ok: true });
      }),
    );
    const { user, dialog } = await openReset();
    await user.type(within(dialog).getByLabelText(/New password/), "brandnewpass");
    const submit = within(dialog).getByRole("button", { name: "Reset password" });

    fireEvent.click(submit);
    expect(submit).toHaveTextContent("Resetting…");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      releaseReset();
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(resetCalls).toBe(1);
  });
});

describe("TeamPage role dialog branches", () => {
  let putBody: Record<string, unknown> | null;
  let postBody: Record<string, unknown> | null;

  beforeEach(() => {
    putBody = null;
    postBody = null;
    server.use(
      teamHandler(),
      http.post("/api/team/roles", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...ROLE_SPARE, id: 14 }, { status: 201 });
      }),
      http.put("/api/team/roles/:roleId", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(ROLE_KEEPER);
      }),
    );
  });

  async function openEditor(roleName: string) {
    const user = userEvent.setup();
    const rendered = await renderLoaded();
    await user.click(within(roleCard(roleName)).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: `Edit role: ${roleName}` });
    return { user, dialog, ...rendered };
  }

  it("marks nothing invalid before the first submit and flags both rejected fields", async () => {
    const { user, dialog } = await openEditor(ROLE_SPARE.name);
    const name = within(dialog).getByLabelText(/Role name/);
    const description = within(dialog).getByLabelText(/Description/);
    expect(name).not.toHaveAttribute("aria-invalid");
    expect(description).not.toHaveAttribute("aria-invalid");
    expect(within(dialog).queryAllByRole("alert")).toHaveLength(0);

    await user.clear(name);
    fireEvent.change(description, { target: { value: "d".repeat(256) } });
    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    expect(await within(dialog).findByText("Name is required")).toBeInTheDocument();
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription("Name is required");
    expect(description).toHaveAttribute("aria-invalid", "true");
    expect(description).toHaveAccessibleDescription(/255 characters/);
    expect(putBody).toBeNull();
  });

  it("keeps the editor open when a dismissal lands in the same tick as the save", async () => {
    let releaseSave!: () => void;
    const saveGate = new Promise<void>((resolve) => {
      releaseSave = resolve;
    });
    server.use(
      http.put("/api/team/roles/:roleId", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        await saveGate;
        return HttpResponse.json(ROLE_SPARE);
      }),
    );
    const { dialog } = await openEditor(ROLE_SPARE.name);
    const submit = within(dialog).getByRole("button", { name: "Save role" });

    fireEvent.click(submit);
    expect(submit).toHaveTextContent("Saving…");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      releaseSave();
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(putBody).toMatchObject({ name: ROLE_SPARE.name, permissions: [] });
  });

  it("annotates the matrix with the dependency and dependant of each permission", async () => {
    const { user, dialog } = await openEditor(ROLE_KEEPER.name);
    const view = permissionRow(dialog, "View animals");
    const create = permissionRow(dialog, "Add animals");

    // animals.create is selected, so its view dependency is held in place.
    expect(within(view).getByRole("checkbox")).toBeChecked();
    expect(within(view).getByRole("checkbox")).toHaveAttribute("aria-disabled", "true");
    expect(within(view).getByText("Required by Add animals.")).toBeInTheDocument();
    expect(within(create).getByRole("checkbox")).not.toHaveAttribute("aria-disabled");
    // Nothing is missing and the owner may grant everything on show.
    expect(within(dialog).queryByText(/^Requires /)).not.toBeInTheDocument();
    expect(within(dialog).queryByText("(retained; you cannot change this)")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("(you cannot grant this)")).not.toBeInTheDocument();
    expect(
      within(dialog).queryByText("(only the farm owner can grant this)"),
    ).not.toBeInTheDocument();

    // Dropping the dependant releases the view permission…
    await user.click(within(create).getByRole("checkbox"));
    await waitFor(() =>
      expect(within(view).queryByText("Required by Add animals.")).not.toBeInTheDocument(),
    );
    expect(within(view).getByRole("checkbox")).not.toHaveAttribute("aria-disabled");

    // …and dropping it in turn flags the gap the action would leave behind.
    await user.click(within(view).getByRole("checkbox"));
    expect(await within(create).findByText("Requires View animals.")).toBeInTheDocument();
    expect(within(dialog).queryByText(/^Required by /)).not.toBeInTheDocument();
  });

  it("lets the farm owner grant team.manage", async () => {
    const { user, dialog } = await openEditor(ROLE_SPARE.name);
    const team = permissionRow(dialog, "Manage team, roles & passwords");
    expect(within(team).getByRole("checkbox")).not.toHaveAttribute("aria-disabled");

    await user.click(within(team).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody).toMatchObject({ permissions: ["team.manage"] });
  });

  it("keeps an ungrantable dependency out of a delegated editor's role", async () => {
    const legacyRole = { ...ROLE_SPARE, id: 15, name: "Legacy entry", permissions: ["animals.create"] };
    server.use(
      permissionsHandler(["team.manage", "animals.create"]),
      teamHandler({ ...TEAM_PAYLOAD, memberships: [MEMBER_SELF], roles: [legacyRole] }),
    );
    const user = userEvent.setup();
    renderWithProviders(<TeamPage />);
    expect(await screen.findByText(TEST_USER.email)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "New role" })).toBeEnabled());
    await user.click(within(roleCard(legacyRole.name)).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: `Edit role: ${legacyRole.name}` });
    const view = permissionRow(dialog, "View animals");
    const create = permissionRow(dialog, "Add animals");
    const team = permissionRow(dialog, "Manage team, roles & passwords");

    // The missing view dependency cannot be repaired by this editor, so it
    // stays unchecked, flagged and out of the payload.
    expect(within(view).getByRole("checkbox")).not.toBeChecked();
    expect(within(view).getByRole("checkbox")).toHaveAttribute("aria-disabled", "true");
    expect(within(view).getByText("(you cannot grant this)")).toBeInTheDocument();
    expect(within(view).getByText("Required by Add animals.")).toBeInTheDocument();
    // The action itself is still theirs to remove.
    expect(within(create).getByRole("checkbox")).toBeChecked();
    expect(within(create).getByRole("checkbox")).not.toHaveAttribute("aria-disabled");
    expect(within(create).getByText("Requires View animals.")).toBeInTheDocument();
    // team.manage is held (this page requires it) but owner-only to grant.
    expect(within(team).getByText("(only the farm owner can grant this)")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody).toMatchObject({ permissions: ["animals.create"] });
  });

  it("refuses to start an action a delegated editor could not complete", async () => {
    server.use(
      permissionsHandler(["team.manage", "animals.create"]),
      teamHandler({ ...TEAM_PAYLOAD, memberships: [MEMBER_SELF], roles: [] }),
    );
    const user = userEvent.setup();
    renderWithProviders(<TeamPage />);
    expect(await screen.findByText(TEST_USER.email)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "New role" })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "New role" }));
    const dialog = await screen.findByRole("dialog", { name: "New role" });
    const create = permissionRow(dialog, "Add animals");

    // animals.create is grantable on its own, but its view dependency is
    // not — offering it would only build a role that cannot reach the page.
    expect(within(create).getByRole("checkbox")).not.toBeChecked();
    expect(within(create).getByRole("checkbox")).toHaveAttribute("aria-disabled", "true");
    expect(within(create).getByText("Requires View animals.")).toBeInTheDocument();
    expect(within(create).queryByText("(you cannot grant this)")).not.toBeInTheDocument();
  });

  it("retains a checked permission the current session cannot grant", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: true, permissions: ["team.manage", "animals.view"] }),
      ),
    );
    const { user, dialog } = await openEditor(ROLE_KEEPER.name);
    const view = permissionRow(dialog, "View animals");
    const create = permissionRow(dialog, "Add animals");

    expect(within(create).getByRole("checkbox")).toBeChecked();
    expect(within(create).getByRole("checkbox")).toHaveAttribute("aria-disabled", "true");
    expect(within(create).getByText("(retained; you cannot change this)")).toBeInTheDocument();
    // The note belongs to the unheld permission only.
    expect(within(view).queryByText("(retained; you cannot change this)")).not.toBeInTheDocument();
    expect(
      within(dialog).getAllByText("(retained; you cannot change this)"),
    ).toHaveLength(1);

    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody).toMatchObject({ permissions: ["animals.view", "animals.create"] });
  });

  it("drops the editor when its role disappears from a refreshed snapshot", async () => {
    let payload: Record<string, unknown> = TEAM_PAYLOAD;
    server.use(http.get("/api/team", () => HttpResponse.json(payload)));
    const { queryClient } = await renderLoaded();
    const user = userEvent.setup();
    await user.click(within(roleCard(ROLE_SPARE.name)).getByRole("button", { name: "Edit" }));
    expect(
      await screen.findByRole("dialog", { name: `Edit role: ${ROLE_SPARE.name}` }),
    ).toBeInTheDocument();

    // Another admin deletes the role while the editor is open.
    payload = {
      ...TEAM_PAYLOAD,
      roles: TEAM_PAYLOAD.roles.filter((role) => role.id !== ROLE_SPARE.id),
    };
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: getTeamPageApiTeamGetQueryKey() });
    });

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(postBody).toBeNull();
  });
});

describe("TeamPage role card and page state branches", () => {
  beforeEach(() => {
    server.use(teamHandler());
  });

  it("refuses a delete retry once the refreshed role has workers again", async () => {
    let deleteCalls = 0;
    let payload: Record<string, unknown> = TEAM_PAYLOAD;
    server.use(
      http.get("/api/team", () => HttpResponse.json(payload)),
      http.delete("/api/team/roles/:roleId", () => {
        deleteCalls += 1;
        return HttpResponse.json({ detail: "role changed concurrently" }, { status: 409 });
      }),
    );
    const user = userEvent.setup();
    const { queryClient } = await renderLoaded();

    await user.click(within(roleCard(ROLE_SPARE.name)).getByRole("button", { name: "Delete" }));
    const confirmDialog = await screen.findByRole("dialog");
    await user.click(within(confirmDialog).getByRole("button", { name: "Delete role" }));
    const card = roleCard(ROLE_SPARE.name);
    expect(await within(card).findByRole("alert")).toHaveTextContent("role changed concurrently");

    // A worker is assigned to the role while the failure is still on screen.
    payload = {
      ...TEAM_PAYLOAD,
      roles: [ROLE_KEEPER, ROLE_HELPER, { ...ROLE_SPARE, member_count: 2 }],
    };
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: getTeamPageApiTeamGetQueryKey() });
    });
    const remove = within(card).getByRole("button", { name: "Delete" });
    await waitFor(() => expect(remove).toBeDisabled());
    expect(remove).toHaveAttribute(
      "title",
      "Role still has workers assigned — reassign them first.",
    );

    // The retry button never carried that guard, so the handler has to
    // re-check it against the refreshed snapshot.
    await user.click(within(card).getByRole("button", { name: "Retry delete role" }));

    expect(deleteCalls).toBe(1);
  });

  it("claims the role card in the same tick a delete starts", async () => {
    server.use(
      http.delete("/api/team/roles/:roleId", () => new HttpResponse(null, { status: 204 })),
    );
    await renderLoaded();
    const card = roleCard(ROLE_SPARE.name);
    const edit = within(card).getByRole("button", { name: "Edit" });

    fireEvent.click(within(card).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete role" }));

    expect(edit).toBeDisabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(ROLE_SPARE.name)).toBeInTheDocument());
  });

  it("shows the roles empty state when the farm has no roles", async () => {
    server.use(
      teamHandler({ ...TEAM_PAYLOAD, memberships: [MEMBER_SELF, MEMBER_PRIYA], roles: [] }),
    );
    renderWithProviders(<TeamPage />);

    expect(await screen.findByText("No roles yet.")).toBeInTheDocument();
    expect(
      screen.getByText("Create a role to control what workers can see and do."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    // The workers table is unaffected by the empty role list.
    expect(screen.getByText(MEMBER_PRIYA.email)).toBeInTheDocument();
  });

  it("waits for permissions instead of flashing an access denial", async () => {
    let releasePerms!: () => void;
    const permsGate = new Promise<void>((resolve) => {
      releasePerms = resolve;
    });
    server.use(
      http.get("/api/auth/permissions", async () => {
        await permsGate;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );
    const { waitForAuthIdle } = renderWithProviders(<TeamPage />);
    await waitForAuthIdle();

    await waitFor(() => expect(screen.getByText("Loading…")).toBeInTheDocument());
    expect(screen.queryByText("You don't have access to this page.")).not.toBeInTheDocument();

    await act(async () => {
      releasePerms();
    });
    expect(await screen.findByText(MEMBER_RAVI.email)).toBeInTheDocument();
  });

  it("hands the reset dialog on to each row that claims it", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Reset password" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(`Reset password — ${MEMBER_RAVI.name}`)).toBeInTheDocument();
    const priyaReset = within(workerRow(MEMBER_PRIYA.email)).getByRole("button", {
      name: "Reset password",
      hidden: true,
    });

    // Both events land before React repaints, so the dismissal still belongs
    // to the superseded target: it must neither close Priya's freshly
    // claimed dialog nor strand the claim on her row.
    act(() => {
      priyaReset.click();
      within(dialog).getByRole("button", { name: "Close" }).click();
    });

    const claimed = await screen.findByRole("dialog");
    expect(within(claimed).getByText(`Reset password — ${MEMBER_PRIYA.name}`)).toBeInTheDocument();
    expect(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", {
        name: "Reset password",
        hidden: true,
      }),
    ).toBeEnabled();

    // Handing the dialog on once more has to release the row it was taken
    // from, which only works while the page still points at the live claim.
    await act(async () => {
      within(workerRow(MEMBER_SITA.email))
        .getByRole("button", { name: "Reset password", hidden: true })
        .click();
    });
    const handedOn = await screen.findByRole("dialog");
    expect(within(handedOn).getByText(`Reset password — ${MEMBER_SITA.email}`)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(workerRow(MEMBER_PRIYA.email)).getByRole("button", {
          name: "Reset password",
          hidden: true,
        }),
      ).toBeEnabled(),
    );

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(
      within(workerRow(MEMBER_SITA.email)).getByRole("button", { name: "Reset password" }),
    ).toBeEnabled();
  });
});

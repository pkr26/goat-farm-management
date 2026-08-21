/**
 * Team page (extended, owner session): workers table (role select, status
 * badge, self guard), add-worker dialog (email/password/role validation +
 * payload mapping), reset-password dialog, deactivate/activate toggle, role
 * cards (preset/member-count delete guards, window.confirm), role create/edit
 * dialogs, error state and RBAC gating. The reduced-permission clamping of
 * the role dialog lives in page.test.tsx.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { getTeamPageApiTeamGetQueryKey } from "@/api/generated/endpoints";
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

const ROLE_UNUSED = {
  id: 12,
  code: null,
  name: "Unused",
  description: null,
  permissions: ["animals.view"],
  revision: 2,
  member_count: 0,
};

const ROLE_PRESET = {
  id: 13,
  code: "MANAGER",
  name: "Manager",
  description: "preset manager role",
  permissions: ["team.manage"],
  revision: 3,
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
  const rendered = renderWithProviders(<TeamPage />);
  expect(await screen.findByText(MEMBER_RAVI.email)).toBeInTheDocument();
  return rendered;
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

  it("activates an inactive worker without asking for deactivation confirmation", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    let body: Record<string, unknown> | null = null;
    server.use(
      http.put("/api/team/workers/3/status", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...MEMBER_SITA, is_active: true });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(
      within(workerRow(MEMBER_SITA.email)).getByRole("button", { name: "Activate" }),
    );

    await waitFor(() => expect(body).toEqual({ is_active: true }));
    expect(confirmSpy).not.toHaveBeenCalled();
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
    let releaseRetry!: () => void;
    let markRetryStarted!: () => void;
    const retryStarted = new Promise<void>((resolve) => {
      markRetryStarted = resolve;
    });
    const retryGate = new Promise<void>((resolve) => {
      releaseRetry = resolve;
    });
    server.use(
      http.post("/api/team/workers/:membershipId/role", async () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json({ detail: "role assignment conflict" }, { status: 409 });
        }
        markRetryStarted();
        await retryGate;
        return HttpResponse.json({ ...MEMBER_RAVI, role_id: 11 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);
    await user.click(within(row).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /Helper/ }));

    expect(await within(row).findByRole("alert")).toHaveTextContent("role assignment conflict");
    await user.click(within(row).getByRole("button", { name: "Retry role change" }));
    await retryStarted;
    expect(within(row).queryByRole("alert")).not.toBeInTheDocument();

    await act(async () => {
      releaseRetry();
    });
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

  it("does not open password reset while a row status change is in flight", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let releaseStatus!: () => void;
    let markStatusStarted!: () => void;
    const statusStarted = new Promise<void>((resolve) => {
      markStatusStarted = resolve;
    });
    const statusGate = new Promise<void>((resolve) => {
      releaseStatus = resolve;
    });
    server.use(
      http.put("/api/team/workers/:membershipId/status", async () => {
        markStatusStarted();
        await statusGate;
        return HttpResponse.json({ ...MEMBER_RAVI, is_active: false });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);

    await user.click(within(row).getByRole("button", { name: "Deactivate" }));
    await statusStarted;
    const reset = within(row).getByRole("button", { name: "Reset password" });
    expect(reset).toBeDisabled();
    await user.click(reset);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await act(async () => {
      releaseStatus();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() => expect(reset).toBeEnabled());
  });

  it("keeps every row action locked until the status refetch becomes authoritative", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let getCalls = 0;
    let announceRefresh: (() => void) | undefined;
    let releaseRefresh: (() => void) | undefined;
    const refreshStarted = new Promise<void>((resolve) => {
      announceRefresh = resolve;
    });
    const refreshGate = new Promise<void>((resolve) => {
      releaseRefresh = resolve;
    });
    server.use(
      http.get("/api/team", async () => {
        getCalls += 1;
        if (getCalls > 1) {
          announceRefresh?.();
          await refreshGate;
          return HttpResponse.json({
            ...TEAM_PAYLOAD,
            memberships: TEAM_PAYLOAD.memberships.map((membership) =>
              membership.id === MEMBER_RAVI.id
                ? { ...MEMBER_RAVI, is_active: false }
                : membership,
            ),
          });
        }
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.put("/api/team/workers/:membershipId/status", () =>
        HttpResponse.json({ ...MEMBER_RAVI, is_active: false }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);

    await user.click(within(row).getByRole("button", { name: "Deactivate" }));
    await refreshStarted;

    // The PUT has completed, but the row still renders its old ACTIVE snapshot.
    // Role/status/password actions must remain visibly and functionally locked
    // until the GET confirms the worker's new state and permission boundary.
    expect(within(row).getByRole("combobox")).toBeDisabled();
    const staleDeactivate = within(row).getByRole("button", { name: "Deactivate" });
    const reset = within(row).getByRole("button", { name: "Reset password" });
    expect(staleDeactivate).toBeDisabled();
    expect(reset).toBeDisabled();
    await user.click(reset);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    releaseRefresh?.();
    await waitFor(() =>
      expect(within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Activate" }))
        .toBeEnabled(),
    );
  });

  it("claims the row synchronously when password reset opens", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    let statusCalls = 0;
    server.use(
      http.put("/api/team/workers/:membershipId/status", () => {
        statusCalls += 1;
        return HttpResponse.json({ ...MEMBER_RAVI, is_active: false });
      }),
    );
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);
    const reset = within(row).getByRole("button", { name: "Reset password" });
    const deactivate = within(row).getByRole("button", { name: "Deactivate" });

    // Deliver both events in one React task, before disabled props can render.
    // The ref claim made by Reset must stop the status request synchronously.
    await act(async () => {
      reset.click();
      deactivate.click();
    });

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(statusCalls).toBe(0);
    const role = within(row).getByRole("combobox", { hidden: true });
    expect(role).toBeDisabled();
    expect(deactivate).toBeDisabled();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(role).toBeEnabled();
    expect(deactivate).toBeEnabled();
  });

  it("rejects role and reset events delivered after a status action claims the row", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let roleCalls = 0;
    let releaseStatus!: () => void;
    let markStatusStarted!: () => void;
    const statusStarted = new Promise<void>((resolve) => {
      markStatusStarted = resolve;
    });
    const statusGate = new Promise<void>((resolve) => {
      releaseStatus = resolve;
    });
    server.use(
      http.put("/api/team/workers/:membershipId/status", async () => {
        markStatusStarted();
        await statusGate;
        return HttpResponse.json({ ...MEMBER_RAVI, is_active: false });
      }),
      http.post("/api/team/workers/:membershipId/role", () => {
        roleCalls += 1;
        return HttpResponse.json({ ...MEMBER_RAVI, role_id: ROLE_HELPER.id });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);
    const deactivate = within(row).getByRole("button", { name: "Deactivate" });
    const reset = within(row).getByRole("button", { name: "Reset password" });

    await user.click(within(row).getByRole("combobox"));
    const helperOption = await screen.findByRole("option", { name: /Helper/ });

    // All three events are delivered before React can paint disabled controls.
    // The ref acquired by the first event is the functional authority here.
    act(() => {
      deactivate.click();
      reset.click();
      helperOption.click();
    });

    await statusStarted;
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(roleCalls).toBe(0);

    await act(async () => {
      releaseStatus();
    });
  });

  it("retries the same desired worker state instead of inverting it", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const bodies: unknown[] = [];
    let releaseRetry!: () => void;
    let markRetryStarted!: () => void;
    const retryStarted = new Promise<void>((resolve) => {
      markRetryStarted = resolve;
    });
    const retryGate = new Promise<void>((resolve) => {
      releaseRetry = resolve;
    });
    server.use(
      teamHandler(),
      http.put("/api/team/workers/:membershipId/status", async ({ request }) => {
        bodies.push(await request.json());
        if (bodies.length === 1) {
          return HttpResponse.json({ detail: "response was interrupted" }, { status: 503 });
        }
        markRetryStarted();
        await retryGate;
        return HttpResponse.json({ ...MEMBER_RAVI, is_active: false });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);
    await user.click(within(row).getByRole("button", { name: "Deactivate" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("response was interrupted");
    await user.click(within(row).getByRole("button", { name: "Retry deactivate" }));
    await retryStarted;
    expect(within(row).queryByRole("alert")).not.toBeInTheDocument();
    expect(bodies).toEqual([{ is_active: false }, { is_active: false }]);

    await act(async () => {
      releaseRetry();
    });
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
    const rendered = await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add worker" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog, ...rendered };
  }

  async function pickRole(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Night Watch" }));
  }

  it("dismisses an idle worker dialog", async () => {
    const { user } = await openDialog();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

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

  it("shows a generic error after a worker-create network failure", async () => {
    server.use(http.post("/api/team/workers", () => HttpResponse.error()));
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Email/), "ravi@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await pickRole(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Something went wrong");
    expect(dialog).toBeInTheDocument();
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

  it("does not dismiss the worker dialog while creation is in flight", async () => {
    let releaseCreate!: () => void;
    let markCreateStarted!: () => void;
    const createGate = new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    const createStarted = new Promise<void>((resolve) => {
      markCreateStarted = resolve;
    });
    server.use(
      http.post("/api/team/workers", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        markCreateStarted();
        await createGate;
        return HttpResponse.json(MEMBER_RAVI, { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await pickRole(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));
    await createStarted;

    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      releaseCreate();
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("keeps the open form inert when its team snapshot starts refreshing", async () => {
    let getCalls = 0;
    let releaseRefresh!: () => void;
    let markRefreshStarted!: () => void;
    const refreshStarted = new Promise<void>((resolve) => {
      markRefreshStarted = resolve;
    });
    const refreshGate = new Promise<void>((resolve) => {
      releaseRefresh = resolve;
    });
    server.use(
      http.get("/api/team", async () => {
        getCalls += 1;
        if (getCalls === 1) return HttpResponse.json(TEAM_PAYLOAD);
        markRefreshStarted();
        await refreshGate;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
    );
    const { user, dialog, queryClient } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await pickRole(user, dialog);

    let refreshPromise!: Promise<void>;
    act(() => {
      refreshPromise = queryClient.invalidateQueries({
        queryKey: getTeamPageApiTeamGetQueryKey(),
      });
    });
    await refreshStarted;

    const fieldset = dialog.querySelector("fieldset") as HTMLFieldSetElement;
    const submit = within(dialog).getByRole("button", { name: "Add worker" });
    await waitFor(() => {
      expect(fieldset).toBeDisabled();
      expect(submit).toBeDisabled();
      expect(submit).toHaveAttribute("disabled");
    });
    expect(screen.getByRole("status", { hidden: true })).toHaveTextContent(
      "Waiting for the latest team data",
    );

    // Exercise the synchronous guard independently of the painted disabled
    // state: stale DOM or an imperative event must not start the POST.
    fieldset.disabled = false;
    (submit as HTMLButtonElement).disabled = false;
    submit.click();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(postCalls).toBe(0);

    await act(async () => {
      releaseRefresh();
      await refreshPromise;
    });
  });

  it("disables submission when no assignable role exists", async () => {
    server.use(teamHandler({ ...TEAM_PAYLOAD, roles: [] }));
    const { dialog } = await openDialog();

    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "You have no roles you are allowed to assign",
    );
    expect(within(dialog).getByRole("button", { name: "Add worker" })).toBeDisabled();
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
    const rendered = await renderLoaded();
    await user.click(within(workerRow(email)).getByRole("button", { name: "Reset password" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog, ...rendered };
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

  it("keeps an open reset form inert while the team snapshot is refreshing", async () => {
    let getCalls = 0;
    let releaseRefresh!: () => void;
    let markRefreshStarted!: () => void;
    const refreshStarted = new Promise<void>((resolve) => {
      markRefreshStarted = resolve;
    });
    const refreshGate = new Promise<void>((resolve) => {
      releaseRefresh = resolve;
    });
    server.use(
      http.get("/api/team", async () => {
        getCalls += 1;
        if (getCalls === 1) return HttpResponse.json(TEAM_PAYLOAD);
        markRefreshStarted();
        await refreshGate;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
    );
    const { user, dialog, queryClient } = await openReset();
    await user.type(within(dialog).getByLabelText(/New password/), "brandnewpass");

    let refreshPromise!: Promise<void>;
    act(() => {
      refreshPromise = queryClient.invalidateQueries({
        queryKey: getTeamPageApiTeamGetQueryKey(),
      });
    });
    await refreshStarted;

    const fieldset = dialog.querySelector("fieldset") as HTMLFieldSetElement;
    const submit = within(dialog).getByRole("button", { name: "Reset password" });
    await waitFor(() => {
      expect(fieldset).toBeDisabled();
      expect(submit).toBeDisabled();
      expect(submit).toHaveAttribute("disabled");
    });

    fieldset.disabled = false;
    (submit as HTMLButtonElement).disabled = false;
    submit.click();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(resetCalls).toBe(0);

    await act(async () => {
      releaseRefresh();
      await refreshPromise;
    });
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

  it("rechecks the preset/member guard if a disabled delete is invoked imperatively", async () => {
    let deleteCalls = 0;
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    server.use(
      http.delete("/api/team/roles/:roleId", () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await renderLoaded();
    const presetDelete = within(cardOf("Manager")).getByRole("button", { name: "Delete" });
    expect(presetDelete).toBeDisabled();

    // The handler itself owns this policy too; bypassing the HTML disabled
    // attribute must still stop before confirmation or a request.
    (presetDelete as HTMLButtonElement).disabled = false;
    presetDelete.click();

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(deleteCalls).toBe(0);
  });

  it("claims delete synchronously against a second delete and edit event", async () => {
    let deleteCalls = 0;
    let releaseDelete!: () => void;
    let markDeleteStarted!: () => void;
    const deleteStarted = new Promise<void>((resolve) => {
      markDeleteStarted = resolve;
    });
    const deleteGate = new Promise<void>((resolve) => {
      releaseDelete = resolve;
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    server.use(
      http.delete("/api/team/roles/:roleId", async () => {
        deleteCalls += 1;
        markDeleteStarted();
        await deleteGate;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await renderLoaded();
    const card = cardOf("Unused");
    const deleteButton = within(card).getByRole("button", { name: "Delete" });
    const editButton = within(card).getByRole("button", { name: "Edit" });

    // React has not painted mutation pending state between these events. The
    // ref must be sufficient to reject both competing operations.
    act(() => {
      deleteButton.click();
      deleteButton.click();
      editButton.click();
    });

    await deleteStarted;
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(deleteCalls).toBe(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await act(async () => {
      releaseDelete();
    });
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

  it("keeps a deleted role card inert until the team refetch removes it", async () => {
    let getCalls = 0;
    let announceRefresh: (() => void) | undefined;
    let releaseRefresh: (() => void) | undefined;
    const refreshStarted = new Promise<void>((resolve) => {
      announceRefresh = resolve;
    });
    const refreshGate = new Promise<void>((resolve) => {
      releaseRefresh = resolve;
    });
    server.use(
      http.get("/api/team", async () => {
        getCalls += 1;
        if (getCalls > 1) {
          announceRefresh?.();
          await refreshGate;
          return HttpResponse.json({
            ...TEAM_PAYLOAD,
            roles: TEAM_PAYLOAD.roles.filter((role) => role.id !== ROLE_UNUSED.id),
          });
        }
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.delete("/api/team/roles/:roleId", () => new HttpResponse(null, { status: 204 })),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    await renderLoaded();
    const card = cardOf("Unused");

    await user.click(within(card).getByRole("button", { name: "Delete" }));
    await refreshStarted;

    const edit = within(card).getByRole("button", { name: "Edit" });
    const deleteButton = within(card).getByRole("button", { name: "Delete" });
    expect(edit).toBeDisabled();
    expect(deleteButton).toBeDisabled();
    await user.click(edit);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    releaseRefresh?.();
    await waitFor(() => expect(screen.queryByText("Unused")).not.toBeInTheDocument());
  });

  it("announces a failed role deletion and offers a confirmed retry", async () => {
    let calls = 0;
    let getCalls = 0;
    let releaseRefresh!: () => void;
    let markRefreshStarted!: () => void;
    let releaseDeleteRetry!: () => void;
    let markDeleteRetryStarted!: () => void;
    const refreshStarted = new Promise<void>((resolve) => {
      markRefreshStarted = resolve;
    });
    const refreshGate = new Promise<void>((resolve) => {
      releaseRefresh = resolve;
    });
    const deleteRetryStarted = new Promise<void>((resolve) => {
      markDeleteRetryStarted = resolve;
    });
    const deleteRetryGate = new Promise<void>((resolve) => {
      releaseDeleteRetry = resolve;
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    server.use(
      http.get("/api/team", async () => {
        getCalls += 1;
        if (getCalls === 1) return HttpResponse.json(TEAM_PAYLOAD);
        markRefreshStarted();
        await refreshGate;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.delete("/api/team/roles/:roleId", async () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json({ detail: "role changed concurrently" }, { status: 409 });
        }
        markDeleteRetryStarted();
        await deleteRetryGate;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    const { queryClient } = await renderLoaded();
    const card = cardOf("Unused");
    await user.click(within(card).getByRole("button", { name: "Delete" }));

    expect(await within(card).findByRole("alert")).toHaveTextContent("role changed concurrently");
    const retry = within(card).getByRole("button", { name: "Retry delete role" });

    let refreshPromise!: Promise<void>;
    act(() => {
      refreshPromise = queryClient.invalidateQueries({
        queryKey: getTeamPageApiTeamGetQueryKey(),
      });
    });
    await refreshStarted;
    await waitFor(() => expect(retry).toBeDisabled());

    await act(async () => {
      releaseRefresh();
      await refreshPromise;
    });
    await waitFor(() => expect(retry).toBeEnabled());
    await user.click(retry);
    await deleteRetryStarted;
    expect(within(card).queryByRole("alert")).not.toBeInTheDocument();

    await act(async () => {
      releaseDeleteRetry();
    });
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

  it("dismisses an idle role dialog", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New role" }));
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
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
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
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

  it("repairs a legacy action-only role before it can be saved", async () => {
    const actionOnlyRole = {
      ...ROLE_UNUSED,
      id: 15,
      name: "Legacy animal entry",
      permissions: ["animals.create"],
    };
    server.use(
      teamHandler({ ...TEAM_PAYLOAD, roles: [...TEAM_PAYLOAD.roles, actionOnlyRole] }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const card = screen
      .getByText("Legacy animal entry")
      .closest("div.rounded-xl") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByRole("checkbox", { name: "Add animals" })).toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: "View animals" })).toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putRoleId).toBe("15");
    expect(putBody).toMatchObject({
      permissions: ["animals.create", "animals.view"],
      expected_revision: actionOnlyRole.revision,
    });
  });

  it("repairs every module action's missing view dependency", async () => {
    const dependencies = {
      "animals.create": "animals.view",
      "animals.move": "animals.view",
      "animals.weight": "animals.view",
      "animals.status": "animals.view",
      "breeding.manage": "breeding.view",
      "kidding.manage": "kidding.view",
      "health.manage": "health.view",
      "purchases.manage": "purchases.view",
      "feeding.manage": "feeding.view",
      "tasks.create": "tasks.view",
      "tasks.complete": "tasks.view",
      "tasks.verify": "tasks.view",
      "finance.manage": "finance.view",
      "simulation.manage": "simulation.view",
    } as const;
    const actionOnlyRole = {
      ...ROLE_UNUSED,
      id: 16,
      name: "Legacy all-action role",
      permissions: Object.keys(dependencies),
    };
    const codes = [...new Set([...Object.keys(dependencies), ...Object.values(dependencies)])];
    server.use(
      teamHandler({
        ...TEAM_PAYLOAD,
        roles: [...TEAM_PAYLOAD.roles, actionOnlyRole],
        permission_groups: [{ group: "All permissions", codes }],
        permission_labels: Object.fromEntries(codes.map((code) => [code, code])),
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const card = screen
      .getByText("Legacy all-action role")
      .closest("div.rounded-xl") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");

    for (const view of new Set(Object.values(dependencies))) {
      expect(within(dialog).getByRole("checkbox", { name: view })).toBeChecked();
    }
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
    expect(putBody).toMatchObject({
      name: "Night Watch",
      permissions: ["animals.view"],
      expected_revision: ROLE_NIGHT_WATCH.revision,
    });
  });

  it("does not dismiss the role editor while a save is in flight", async () => {
    let releaseSave!: () => void;
    let markSaveStarted!: () => void;
    const saveGate = new Promise<void>((resolve) => {
      releaseSave = resolve;
    });
    const saveStarted = new Promise<void>((resolve) => {
      markSaveStarted = resolve;
    });
    server.use(
      http.put("/api/team/roles/10", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        putRoleId = "10";
        markSaveStarted();
        await saveGate;
        return HttpResponse.json(ROLE_NIGHT_WATCH);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const card = screen
      .getAllByText("Night Watch")
      .map((element) => element.closest("div.rounded-xl") as HTMLElement | null)
      .find((element) => element && within(element).queryByRole("button", { name: "Edit" }))!;
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Save role" }));
    await saveStarted;

    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      releaseSave();
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("refreshes a conflicted role before retrying its optimistic revision", async () => {
    const refreshedRole = {
      ...ROLE_NIGHT_WATCH,
      name: "Night Watch remote",
      revision: ROLE_NIGHT_WATCH.revision + 1,
    };
    let currentPayload = TEAM_PAYLOAD;
    const revisions: number[] = [];
    server.use(
      http.get("/api/team", () => HttpResponse.json(currentPayload)),
      http.put("/api/team/roles/10", async ({ request }) => {
        const body = (await request.json()) as { expected_revision: number };
        revisions.push(body.expected_revision);
        if (revisions.length === 1) {
          currentPayload = {
            ...TEAM_PAYLOAD,
            roles: TEAM_PAYLOAD.roles.map((role) =>
              role.id === refreshedRole.id ? refreshedRole : role,
            ),
          };
          return HttpResponse.json({ detail: "role changed concurrently" }, { status: 409 });
        }
        return HttpResponse.json(refreshedRole);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const card = screen
      .getAllByText("Night Watch")
      .map((element) => element.closest("div.rounded-xl") as HTMLElement | null)
      .find((element) => element && within(element).queryByRole("button", { name: "Edit" }))!;
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Save role" }),
    );

    const refreshedDialog = await screen.findByRole("dialog", {
      name: "Edit role: Night Watch remote",
    });
    expect(within(refreshedDialog).getByLabelText(/Role name/)).toHaveValue(
      "Night Watch remote",
    );
    await user.click(within(refreshedDialog).getByRole("button", { name: "Save role" }));

    await waitFor(() => expect(revisions).toEqual([4, 5]));
  });

  it("does not refresh or remount the editor for a non-conflict save error", async () => {
    let getCalls = 0;
    server.use(
      http.get("/api/team", () => {
        getCalls += 1;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.put("/api/team/roles/10", () =>
        HttpResponse.json({ detail: "role name is reserved" }, { status: 400 }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const card = screen
      .getAllByText("Night Watch")
      .map((element) => element.closest("div.rounded-xl") as HTMLElement | null)
      .find((element) => element && within(element).queryByRole("button", { name: "Edit" }))!;
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit role: Night Watch" });

    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("role name is reserved");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(getCalls).toBe(1);
    expect(screen.getByRole("dialog", { name: "Edit role: Night Watch" })).toBe(dialog);
  });

  it("keeps an open role form inert while the team snapshot is refreshing", async () => {
    let getCalls = 0;
    let releaseRefresh!: () => void;
    let markRefreshStarted!: () => void;
    const refreshStarted = new Promise<void>((resolve) => {
      markRefreshStarted = resolve;
    });
    const refreshGate = new Promise<void>((resolve) => {
      releaseRefresh = resolve;
    });
    server.use(
      http.get("/api/team", async () => {
        getCalls += 1;
        if (getCalls === 1) return HttpResponse.json(TEAM_PAYLOAD);
        markRefreshStarted();
        await refreshGate;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
    );
    const user = userEvent.setup();
    const { queryClient } = await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New role" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Role name/), "Relief worker");

    let refreshPromise!: Promise<void>;
    act(() => {
      refreshPromise = queryClient.invalidateQueries({
        queryKey: getTeamPageApiTeamGetQueryKey(),
      });
    });
    await refreshStarted;

    const fieldset = dialog.querySelector("fieldset") as HTMLFieldSetElement;
    const submit = within(dialog).getByRole("button", { name: "Create role" });
    await waitFor(() => {
      expect(fieldset).toBeDisabled();
      expect(submit).toBeDisabled();
      expect(submit).toHaveAttribute("disabled");
    });

    fieldset.disabled = false;
    (submit as HTMLButtonElement).disabled = false;
    submit.click();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(postBody).toBeNull();

    await act(async () => {
      releaseRefresh();
      await refreshPromise;
    });
  });
});

describe("TeamPage global team-snapshot authority", () => {
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

  function expectMutablePageControlsToBeDisabled() {
    const ravi = workerRow(MEMBER_RAVI.email);
    const sita = workerRow(MEMBER_SITA.email);
    const unused = roleCard(ROLE_UNUSED.name);

    expect(screen.getByRole("button", { name: "Add worker" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "New role" })).toBeDisabled();
    expect(within(ravi).getByRole("combobox")).toBeDisabled();
    expect(within(ravi).getByRole("button", { name: "Deactivate" })).toBeDisabled();
    expect(within(ravi).getByRole("button", { name: "Reset password" })).toBeDisabled();
    expect(within(sita).getByRole("button", { name: "Activate" })).toBeDisabled();
    expect(within(unused).getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(within(unused).getByRole("button", { name: "Delete" })).toBeDisabled();
  }

  it("rejects stale same-render events as soon as a replacement GET claims authority", async () => {
    let getCalls = 0;
    let roleCalls = 0;
    let statusCalls = 0;
    let deleteCalls = 0;
    let releaseRefresh!: () => void;
    let markRefreshStarted!: () => void;
    const refreshStarted = new Promise<void>((resolve) => {
      markRefreshStarted = resolve;
    });
    const refreshGate = new Promise<void>((resolve) => {
      releaseRefresh = resolve;
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    server.use(
      http.get("/api/team", async () => {
        getCalls += 1;
        if (getCalls === 1) return HttpResponse.json(TEAM_PAYLOAD);
        markRefreshStarted();
        await refreshGate;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.post("/api/team/workers/:membershipId/role", () => {
        roleCalls += 1;
        return HttpResponse.json({ ...MEMBER_RAVI, role_id: ROLE_HELPER.id });
      }),
      http.put("/api/team/workers/:membershipId/status", () => {
        statusCalls += 1;
        return HttpResponse.json({ ...MEMBER_RAVI, is_active: false });
      }),
      http.delete("/api/team/roles/:roleId", () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    const { queryClient } = await renderLoaded();
    const ravi = workerRow(MEMBER_RAVI.email);
    const unused = roleCard(ROLE_UNUSED.name);
    const reset = within(ravi).getByRole("button", { name: "Reset password" });
    const deactivate = within(ravi).getByRole("button", { name: "Deactivate" });
    const edit = within(unused).getByRole("button", { name: "Edit" });
    const deleteButton = within(unused).getByRole("button", { name: "Delete" });
    const addWorker = screen.getByRole("button", { name: "Add worker" });
    const newRole = screen.getByRole("button", { name: "New role" });
    await user.click(within(ravi).getByRole("combobox"));
    const helperOption = await screen.findByRole("option", { name: /Helper/ });

    let refreshPromise!: Promise<void>;
    act(() => {
      // Query state changes synchronously, while React's disabled props are
      // still from the prior render. Every handler must consult cache state.
      refreshPromise = queryClient.invalidateQueries({
        queryKey: getTeamPageApiTeamGetQueryKey(),
      });
      helperOption.click();
      reset.click();
      deactivate.click();
      deleteButton.click();
      edit.click();
      addWorker.click();
      newRole.click();
    });
    await refreshStarted;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(roleCalls).toBe(0);
    expect(statusCalls).toBe(0);
    expect(deleteCalls).toBe(0);
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Waiting for the latest team data");

    await act(async () => {
      releaseRefresh();
      await refreshPromise;
    });
  });

  it("rejects a same-render action after cache status loses success authority", async () => {
    server.use(teamHandler());
    const { queryClient } = await renderLoaded();
    const newRole = screen.getByRole("button", { name: "New role" });
    const teamQuery = queryClient.getQueryCache().find({
      queryKey: getTeamPageApiTeamGetQueryKey(),
    });
    expect(teamQuery).toBeDefined();

    act(() => {
      // Observer notification is batched, so this button still belongs to the
      // last successful render. canStart must read the cache synchronously.
      teamQuery!.setState({
        ...teamQuery!.state,
        status: "error",
        fetchStatus: "idle",
        error: new Error("team snapshot became stale"),
      });
      newRole.click();
    });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Team data could not be refreshed",
    );
  });

  it("stays locked when a second mutation cancels and replaces the first invalidation GET", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let getCalls = 0;
    let releaseDelete!: () => void;
    let announceDeleteStarted!: () => void;
    let announceFirstRefreshStarted!: () => void;
    let announceFirstRefreshAborted!: () => void;
    let announceReplacementStarted!: () => void;
    let releaseReplacement!: () => void;
    const deleteStarted = new Promise<void>((resolve) => {
      announceDeleteStarted = resolve;
    });
    const deleteGate = new Promise<void>((resolve) => {
      releaseDelete = resolve;
    });
    const firstRefreshStarted = new Promise<void>((resolve) => {
      announceFirstRefreshStarted = resolve;
    });
    const firstRefreshAborted = new Promise<void>((resolve) => {
      announceFirstRefreshAborted = resolve;
    });
    const replacementStarted = new Promise<void>((resolve) => {
      announceReplacementStarted = resolve;
    });
    const replacementGate = new Promise<void>((resolve) => {
      releaseReplacement = resolve;
    });

    server.use(
      http.get("/api/team", async ({ request }) => {
        getCalls += 1;
        if (getCalls === 1) return HttpResponse.json(TEAM_PAYLOAD);
        if (getCalls === 2) {
          const aborted = new Promise<void>((resolve) => {
            if (request.signal.aborted) {
              resolve();
              return;
            }
            request.signal.addEventListener("abort", () => resolve(), { once: true });
          });
          announceFirstRefreshStarted();
          await aborted;
          announceFirstRefreshAborted();
          return HttpResponse.json(TEAM_PAYLOAD);
        }

        announceReplacementStarted();
        await replacementGate;
        return HttpResponse.json({
          ...TEAM_PAYLOAD,
          memberships: TEAM_PAYLOAD.memberships.map((membership) =>
            membership.id === MEMBER_RAVI.id
              ? { ...MEMBER_RAVI, is_active: false }
              : membership,
          ),
          roles: TEAM_PAYLOAD.roles.filter((role) => role.id !== ROLE_UNUSED.id),
        });
      }),
      http.put("/api/team/workers/:membershipId/status", () =>
        HttpResponse.json({ ...MEMBER_RAVI, is_active: false }),
      ),
      http.delete("/api/team/roles/:roleId", async () => {
        announceDeleteStarted();
        await deleteGate;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await renderLoaded();
    const ravi = workerRow(MEMBER_RAVI.email);
    const unused = roleCard(ROLE_UNUSED.name);

    // Both handlers claim their mutations before React can paint the global
    // authority lock. The status write finishes first and starts GET #2.
    act(() => {
      within(ravi).getByRole("button", { name: "Deactivate" }).click();
      within(unused).getByRole("button", { name: "Delete" }).click();
    });
    await deleteStarted;
    await firstRefreshStarted;
    await waitFor(() => expectMutablePageControlsToBeDisabled());
    const addWorker = screen.getByRole("button", { name: "Add worker" });
    let observedUnlockedGap = false;
    const authorityObserver = new MutationObserver(() => {
      if (!addWorker.hasAttribute("disabled")) observedUnlockedGap = true;
    });
    authorityObserver.observe(addWorker, { attributes: true, attributeFilter: ["disabled"] });

    // Finishing the second write invalidates the same key: TanStack aborts
    // GET #2 and starts GET #3. A waiter released by the abort must not make
    // any stale mutation control authoritative while GET #3 is pending.
    await act(async () => {
      releaseDelete();
      await firstRefreshAborted;
      await replacementStarted;
    });
    expect(getCalls).toBe(3);
    await waitFor(() => expectMutablePageControlsToBeDisabled());
    expect(observedUnlockedGap).toBe(false);

    await act(async () => {
      releaseReplacement();
    });
    await waitFor(() =>
      expect(within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Activate" }))
        .toBeEnabled(),
    );
    expect(screen.getByRole("button", { name: "Add worker" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "New role" })).toBeEnabled();
    expect(screen.queryByText(ROLE_UNUSED.name)).not.toBeInTheDocument();
    authorityObserver.disconnect();
  });

  it("keeps stale team data visible but all mutations disabled after a refresh failure", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let getCalls = 0;
    let failRefresh = true;
    server.use(
      http.get("/api/team", () => {
        getCalls += 1;
        if (getCalls === 1) return HttpResponse.json(TEAM_PAYLOAD);
        if (failRefresh) {
          return HttpResponse.json({ detail: "refresh failed" }, { status: 503 });
        }
        return HttpResponse.json({
          ...TEAM_PAYLOAD,
          memberships: TEAM_PAYLOAD.memberships.map((membership) =>
            membership.id === MEMBER_RAVI.id
              ? { ...MEMBER_RAVI, is_active: false }
              : membership,
          ),
        });
      }),
      http.put("/api/team/workers/:membershipId/status", () =>
        HttpResponse.json({ ...MEMBER_RAVI, is_active: false }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Deactivate" }),
    );

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(
      "Team data could not be refreshed. Actions are disabled until the latest team snapshot loads.",
    );
    // The successful initial snapshot remains rendered, but it is explicitly
    // stale: even controls unrelated to the completed status write are inert.
    expect(screen.getByText(MEMBER_RAVI.email)).toBeInTheDocument();
    expectMutablePageControlsToBeDisabled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // status=error/fetchStatus=idle is not authoritative. Bypass the painted
    // disabled attribute to exercise both halves of canStart directly.
    const staleNewRole = screen.getByRole("button", { name: "New role" });
    (staleNewRole as HTMLButtonElement).disabled = false;
    staleNewRole.click();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    failRefresh = false;
    await user.click(within(banner).getByRole("button", { name: "Retry team refresh" }));

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(getCalls).toBe(3);
    expect(within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Activate" }))
      .toBeEnabled();
    expect(screen.getByRole("button", { name: "Add worker" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "New role" })).toBeEnabled();
    expect(within(roleCard(ROLE_UNUSED.name)).getByRole("button", { name: "Edit" }))
      .toBeEnabled();
    expect(within(roleCard(ROLE_UNUSED.name)).getByRole("button", { name: "Delete" }))
      .toBeEnabled();
  });
});

describe("TeamPage RBAC and errors", () => {
  it("fails closed when permissions cannot be loaded", async () => {
    let calls = 0;
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 }),
      ),
      http.get("/api/team", () => {
        calls += 1;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
    );
    renderWithProviders(<TeamPage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    expect(calls).toBe(0);
  });

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

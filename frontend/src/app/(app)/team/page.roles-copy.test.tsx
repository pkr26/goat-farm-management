/**
 * Team page — a second pass over the exact words and wiring the page owes its
 * operators: the label every role select shows, worker initials, the accessible
 * association of each validation message, the permission matrix's control ids
 * and dependency notes, in-flight button labels, the delete/edit hints on role
 * cards, and every success/failure toast. Flow coverage lives in
 * page.extended.test.tsx; the reduced-permission clamping in page.test.tsx.
 */

import { configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ALL_PERMISSIONS, permissionsHandler, server, TEST_USER } from "@/test/msw-server";
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
  // file shares a loaded machine with the rest of the gate.
  configure({ asyncUtilTimeout: 3000 });
  // jsdom lacks the pointer-capture/scroll APIs Base UI Select relies on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
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
  role_id: ROLE_NIGHT_WATCH.id,
  role_name: ROLE_NIGHT_WATCH.name,
  is_active: true,
  can_reset_password: true,
  reset_password_block_reason: null,
};

/** No name at all: every label must fall back to the email. */
const MEMBER_SITA = {
  id: 3,
  user_id: 23,
  email: "sita@example.com",
  name: null,
  role_id: ROLE_HELPER.id,
  role_name: ROLE_HELPER.name,
  is_active: true,
  can_reset_password: true,
  reset_password_block_reason: null,
};

/** Padded, lower-case legacy name: the avatar must still read "M". */
const MEMBER_MEENA = {
  id: 4,
  user_id: 24,
  email: "meena@example.com",
  name: "  meena kale  ",
  role_id: ROLE_HELPER.id,
  role_name: ROLE_HELPER.name,
  is_active: false,
  can_reset_password: true,
  reset_password_block_reason: null,
};

const PERMISSION_GROUPS = [
  {
    group: "Animals",
    codes: ["animals.view", "animals.create", "animals.move", "animals.weight", "animals.status"],
  },
  {
    group: "Tasks / duties",
    codes: ["tasks.view", "tasks.create", "tasks.complete", "tasks.verify"],
  },
  { group: "Team", codes: ["team.manage"] },
];

const PERMISSION_LABELS: Record<string, string> = {
  "animals.view": "View animals",
  "animals.create": "Add animals",
  "animals.move": "Move animals",
  "animals.weight": "Record weights",
  "animals.status": "Change animal status",
  "tasks.view": "View assigned duties",
  "tasks.create": "Assign duties",
  "tasks.complete": "Complete duties",
  "tasks.verify": "Verify duties",
  "team.manage": "Manage team, roles & passwords",
};

const TEAM_PAYLOAD = {
  memberships: [MEMBER_SELF, MEMBER_RAVI, MEMBER_SITA, MEMBER_MEENA],
  roles: [ROLE_NIGHT_WATCH, ROLE_HELPER, ROLE_PRESET],
  permission_groups: PERMISSION_GROUPS,
  permission_labels: PERMISSION_LABELS,
};

function teamHandler(payload: Record<string, unknown> = TEAM_PAYLOAD) {
  return http.get("/api/team", () => HttpResponse.json(payload));
}

function workerRow(email: string): HTMLElement {
  // Worker content also renders in the below-md card list (md:hidden) — scope
  // to the desktop table so duplicated text stays unambiguous.
  const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
  const row = within(table).getByText(email).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

/** Role card root: role names also appear in the row select triggers, so the
 * card is the rounded container that owns an Edit button. */
function roleCard(name: string): HTMLElement {
  const card = screen
    .getAllByText(name)
    .map((node) => node.closest("div.rounded-xl"))
    .find(
      (node): node is HTMLElement =>
        node instanceof HTMLElement &&
        within(node).queryByRole("button", { name: "Edit" }) !== null,
    );
  expect(card).toBeDefined();
  return card as HTMLElement;
}

/** A response the test releases by hand, to observe in-flight labels. */
function createGate() {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { gate, release };
}

async function renderLoaded() {
  const rendered = renderWithProviders(<TeamPage />);
  expect((await screen.findAllByText(MEMBER_RAVI.email)).length).toBeGreaterThan(0);
  await waitFor(() => expect(screen.getByRole("button", { name: "New role" })).toBeEnabled());
  return rendered;
}

type User = ReturnType<typeof userEvent.setup>;

async function openWorkerDialog(user: User) {
  await user.click(screen.getByRole("button", { name: "Add worker" }));
  return screen.findByRole("dialog");
}

async function openNewRoleDialog(user: User) {
  await user.click(screen.getByRole("button", { name: "New role" }));
  return screen.findByRole("dialog");
}

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("TeamPage worker rows", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server.use(teamHandler());
  });

  it("labels each role select by worker and shows a trimmed upper-case initial", async () => {
    await renderLoaded();

    const ravi = workerRow(MEMBER_RAVI.email);
    expect(
      within(ravi).getByRole("combobox", { name: "Role for Ravi Kumar" }),
    ).toBeInTheDocument();
    expect(within(ravi).getByText("R")).toBeInTheDocument();

    // A nameless worker is identified by their email everywhere.
    expect(
      within(workerRow(MEMBER_SITA.email)).getByRole("combobox", {
        name: "Role for sita@example.com",
      }),
    ).toBeInTheDocument();

    // Legacy padding/casing must not leak into the avatar.
    expect(within(workerRow(MEMBER_MEENA.email)).getByText("M")).toBeInTheDocument();

    // A membership without a role reads as "No role" — never the sentinel value,
    // and it is a real selection rather than an empty placeholder (which would
    // render the label in muted placeholder styling).
    const roleless = within(workerRow(TEST_USER.email)).getByRole("combobox");
    expect(roleless).toHaveTextContent("No role");
    expect(roleless).not.toHaveAttribute("data-placeholder");
  });

  it("refetches the team AND this session's permissions after a role change", async () => {
    let permissionCalls = 0;
    let teamCalls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        permissionCalls += 1;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
      http.get("/api/team", () => {
        teamCalls += 1;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.post("/api/team/workers/:membershipId/role", () =>
        HttpResponse.json({ ...MEMBER_RAVI, role_id: ROLE_HELPER.id }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const teamCallsBefore = teamCalls;
    const permissionCallsBefore = permissionCalls;

    await pickOption(
      user,
      within(workerRow(MEMBER_RAVI.email)).getByRole("combobox"),
      ROLE_HELPER.name,
    );

    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Role updated."));
    // A role edit can change the EDITOR's own grants, so both the team
    // snapshot and /api/auth/permissions refresh (stale-permission UI, M-5).
    await waitFor(() => expect(teamCalls).toBeGreaterThan(teamCallsBefore));
    await waitFor(() => expect(permissionCalls).toBeGreaterThan(permissionCallsBefore));
  });

  it("names the worker state each status toast reports", async () => {
    server.use(
      http.put("/api/team/workers/:membershipId/status", () =>
        HttpResponse.json({ ...MEMBER_RAVI, is_active: false }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Deactivate" }),
    );
    const deactivation = await screen.findByRole("dialog");
    await user.click(
      within(deactivation).getByRole("button", { name: "Deactivate worker" }),
    );
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Worker deactivated."));

    // The refreshed snapshot re-arms every row before the next action.
    await waitFor(() =>
      expect(
        within(workerRow(MEMBER_MEENA.email)).getByRole("button", { name: "Activate" }),
      ).toBeEnabled(),
    );
    await user.click(
      within(workerRow(MEMBER_MEENA.email)).getByRole("button", { name: "Activate" }),
    );
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Worker activated."));
  });

  it("toasts a failed role change alongside the inline alert", async () => {
    server.use(
      http.post("/api/team/workers/:membershipId/role", () =>
        HttpResponse.json({ detail: "that role no longer exists" }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);

    await pickOption(user, within(row).getByRole("combobox"), ROLE_HELPER.name);

    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "that role no longer exists",
    );
    expect(toastMock.error).toHaveBeenCalledWith("that role no longer exists");
  });

  it("offers 'Retry activate' after a failed activation", async () => {
    server.use(
      http.put("/api/team/workers/:membershipId/status", () =>
        HttpResponse.json({ detail: "this membership was removed" }, { status: 404 }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_MEENA.email);

    await user.click(within(row).getByRole("button", { name: "Activate" }));

    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "this membership was removed",
    );
    // The retry must name the state it will ask for, not the opposite one.
    expect(within(row).getByRole("button", { name: "Retry activate" })).toBeInTheDocument();
    expect(toastMock.error).toHaveBeenCalledWith("this membership was removed");
  });
});

describe("TeamPage add-worker dialog copy", () => {
  let postCalls: number;
  let postBody: Record<string, unknown> | null;

  beforeEach(() => {
    vi.clearAllMocks();
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

  it("describes each role by name and description in the list and the closed trigger", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openWorkerDialog(user);
    const trigger = within(dialog).getByRole("combobox");

    await user.click(trigger);
    expect(
      await screen.findByRole("option", { name: "Helper — general farm help" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Night Watch" })).toBeInTheDocument();

    await user.click(screen.getByRole("option", { name: "Helper — general farm help" }));
    expect(within(trigger).getByText("Helper — general farm help")).toBeInTheDocument();

    // A role without a description gets the bare name — no dangling separator.
    await pickOption(user, trigger, "Night Watch");
    expect(within(trigger).getByText("Night Watch")).toBeInTheDocument();
  });

  it("associates an over-long name and still accepts the padded boundary", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openWorkerDialog(user);
    const name = within(dialog).getByLabelText("Name");

    // fireEvent intentionally bypasses the browser's maxLength guard so the
    // schema stays the authority for pasted values.
    fireEvent.change(name, { target: { value: "a".repeat(121) } });
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    await waitFor(() =>
      expect(name).toHaveAttribute("aria-describedby", "worker-name-error"),
    );
    expect(name).toHaveAccessibleDescription(/120 characters/);
    expect(postCalls).toBe(0);

    // 120 characters plus padding is inside the limit once trimmed.
    fireEvent.change(name, { target: { value: `${"a".repeat(120)}  ` } });
    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "supersecret12");
    await pickOption(user, within(dialog).getByRole("combobox"), "Night Watch");
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ name: "a".repeat(120) });
  });

  it("rejects a password above the API limit and associates the message", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openWorkerDialog(user);
    const password = within(dialog).getByLabelText(/Password/);

    fireEvent.change(password, { target: { value: "p".repeat(129) } });
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(
      await within(dialog).findByText("Password must be at most 128 characters"),
    ).toBeInTheDocument();
    expect(password).toHaveAttribute("aria-describedby", "worker-password-error");
    expect(postCalls).toBe(0);
  });

  it("clears the role error as soon as a role is picked", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openWorkerDialog(user);
    const trigger = within(dialog).getByRole("combobox");

    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(await within(dialog).findByText("Pick a role")).toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-describedby", "worker-role-error");

    await pickOption(user, trigger, "Night Watch");

    // Picking revalidates: the operator must not be left staring at a
    // contradicted error until they submit again.
    await waitFor(() => expect(within(dialog).queryByText("Pick a role")).not.toBeInTheDocument());
    expect(trigger).not.toHaveAttribute("aria-describedby");
  });

  it("accepts a pasted address whose leading non-breaking space the input keeps", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openWorkerDialog(user);

    // type=email strips ASCII padding itself; U+00A0 survives it and only the
    // schema's trim can save the address from failing the email check.
    fireEvent.change(within(dialog).getByLabelText(/Email/), {
      target: { value: "\u00a0new@example.com" },
    });
    await user.type(within(dialog).getByLabelText(/Password/), "supersecret12");
    await pickOption(user, within(dialog).getByRole("combobox"), "Night Watch");
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ email: "new@example.com" });
  });

  it("labels the in-flight submit, toasts the addition and reopens an empty form", async () => {
    const { gate, release } = createGate();
    server.use(
      http.post("/api/team/workers", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        await gate;
        return HttpResponse.json(MEMBER_RAVI, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openWorkerDialog(user);
    await user.type(within(dialog).getByLabelText("Name"), "Meena Kale");
    await user.type(within(dialog).getByLabelText(/Email/), "meena@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "supersecret12");
    await pickOption(user, within(dialog).getByRole("combobox"), "Night Watch");

    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(await within(dialog).findByRole("button", { name: "Adding…" })).toBeDisabled();
    release();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Worker added."));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const reopened = await openWorkerDialog(user);
    expect(within(reopened).getByLabelText("Name")).toHaveValue("");
    expect(within(reopened).getByLabelText(/Email/)).toHaveValue("");
    expect(within(reopened).getByLabelText(/Password/)).toHaveValue("");
    // The trigger must not keep advertising a role the reset form no longer holds.
    expect(within(reopened).getByRole("combobox")).not.toHaveTextContent("Night Watch");
  });

  it("clears an in-progress worker draft when the dialog is dismissed and reopened", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openWorkerDialog(user);
    await user.type(within(dialog).getByLabelText("Name"), "Meena Kale");
    await user.type(within(dialog).getByLabelText(/Email/), "meena@example.com");
    await pickOption(user, within(dialog).getByRole("combobox"), "Night Watch");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const reopened = await openWorkerDialog(user);

    // RT-P2-1: dismissal now clears the form exactly like the Cancel button
    // (the dialog stays mounted, and a half-typed password must not survive
    // into the next open) — including the role, whose trigger must not keep
    // advertising a value the reset form no longer holds.
    expect(within(reopened).getByLabelText("Name")).toHaveValue("");
    expect(within(reopened).getByLabelText(/Email/)).toHaveValue("");
    expect(within(reopened).getByRole("combobox")).not.toHaveTextContent("Night Watch");
  });

  it("toasts a rejected worker and forgets the error once the dialog is dismissed", async () => {
    server.use(
      http.post("/api/team/workers", () => {
        postCalls += 1;
        return HttpResponse.json({ detail: "email already on this farm" }, { status: 400 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openWorkerDialog(user);
    await user.type(within(dialog).getByLabelText(/Email/), "ravi@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "supersecret12");
    await pickOption(user, within(dialog).getByRole("combobox"), "Night Watch");
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "email already on this farm",
    );
    expect(toastMock.error).toHaveBeenCalledWith("email already on this farm");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const reopened = await openWorkerDialog(user);
    expect(within(reopened).queryByText("email already on this farm")).not.toBeInTheDocument();
    expect(within(reopened).getByRole("button", { name: "Add worker" })).toBeInTheDocument();
  });

  it("drops the previous error the moment a retry starts", async () => {
    const { gate, release } = createGate();
    server.use(
      http.post("/api/team/workers", async () => {
        postCalls += 1;
        if (postCalls === 1) {
          return HttpResponse.json({ detail: "email already on this farm" }, { status: 400 });
        }
        await gate;
        return HttpResponse.json(MEMBER_RAVI, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openWorkerDialog(user);
    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "supersecret12");
    await pickOption(user, within(dialog).getByRole("combobox"), "Night Watch");
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));
    await within(dialog).findByText("email already on this farm");

    await user.click(within(dialog).getByRole("button", { name: "Retry add worker" }));

    expect(await within(dialog).findByRole("button", { name: "Adding…" })).toBeInTheDocument();
    expect(within(dialog).queryByText("email already on this farm")).not.toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("TeamPage reset-password dialog copy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server.use(teamHandler());
  });

  async function openReset(user: User, email = MEMBER_RAVI.email) {
    await user.click(within(workerRow(email)).getByRole("button", { name: "Reset password" }));
    return screen.findByRole("dialog");
  }

  it("holds an untouched form to the same minimum-length message", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openReset(user);

    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));

    expect(
      await within(dialog).findByText("Password must be at least 12 characters"),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/New password/)).toHaveAttribute(
      "aria-describedby",
      "reset-password-error",
    );
  });

  it("labels the in-flight submit and toasts the completed reset", async () => {
    const { gate, release } = createGate();
    server.use(
      http.post("/api/team/workers/:membershipId/reset-password", async () => {
        await gate;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openReset(user);
    await user.type(within(dialog).getByLabelText(/New password/), "supersecret12");

    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));

    expect(await within(dialog).findByRole("button", { name: "Resetting…" })).toBeDisabled();
    release();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Password reset."));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("toasts a rejected reset and clears the message when the retry starts", async () => {
    const { gate, release } = createGate();
    let calls = 0;
    server.use(
      http.post("/api/team/workers/:membershipId/reset-password", async () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json({ detail: "password is in the breach list" }, { status: 400 });
        }
        await gate;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openReset(user);
    await user.type(within(dialog).getByLabelText(/New password/), "supersecret12");
    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "password is in the breach list",
    );
    expect(toastMock.error).toHaveBeenCalledWith("password is in the breach list");

    await user.click(within(dialog).getByRole("button", { name: "Retry password reset" }));

    expect(await within(dialog).findByRole("button", { name: "Resetting…" })).toBeInTheDocument();
    expect(
      within(dialog).queryByText("password is in the breach list"),
    ).not.toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("keeps a newer reset dialog open when the previous one finishes", async () => {
    const { gate, release } = createGate();
    let calls = 0;
    server.use(
      http.post("/api/team/workers/:membershipId/reset-password", async () => {
        calls += 1;
        await gate;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const first = await openReset(user);
    await user.type(within(first).getByLabelText(/New password/), "supersecret12");
    await user.click(within(first).getByRole("button", { name: "Reset password" }));
    await waitFor(() => expect(calls).toBe(1));

    // A second worker's reset is claimed while the first is still on the wire.
    // The open modal hides the row from assistive queries, so reach for the
    // button directly — the handler, not the overlay, owns this policy.
    fireEvent.click(
      within(workerRow(MEMBER_SITA.email)).getByRole("button", {
        name: "Reset password",
        hidden: true,
      }),
    );
    expect(
      await screen.findByText(`Reset password — ${MEMBER_SITA.email}`),
    ).toBeInTheDocument();

    release();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Password reset."));
    // The finished dialog owns its own target only — it must not close the
    // dialog that replaced it.
    expect(screen.getByText(`Reset password — ${MEMBER_SITA.email}`)).toBeInTheDocument();
  });
});

describe("TeamPage role dialog copy", () => {
  let postBody: Record<string, unknown> | null;
  let postCalls: number;

  beforeEach(() => {
    vi.clearAllMocks();
    postBody = null;
    postCalls = 0;
    server.use(
      teamHandler(),
      http.post("/api/team/roles", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...ROLE_HELPER, id: 14 }, { status: 201 });
      }),
    );
  });

  it("titles a new role dialog and refuses a whitespace-only name", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openNewRoleDialog(user);

    expect(within(dialog).getByRole("heading", { name: "New role" })).toBeInTheDocument();

    const name = within(dialog).getByLabelText(/Role name/);
    await user.type(name, "   ");
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    expect(await within(dialog).findByText("Name is required")).toBeInTheDocument();
    expect(name).toHaveAttribute("aria-describedby", "role-name-error");
    expect(postCalls).toBe(0);
  });

  it("associates an over-long description and accepts the padded boundary", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openNewRoleDialog(user);
    const description = within(dialog).getByLabelText("Description");

    await user.type(within(dialog).getByLabelText(/Role name/), "Night Watchman");
    fireEvent.change(description, { target: { value: "d".repeat(256) } });
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    await waitFor(() =>
      expect(description).toHaveAttribute("aria-describedby", "role-description-error"),
    );
    expect(description).toHaveAccessibleDescription(/255 characters/);
    expect(postCalls).toBe(0);

    fireEvent.change(description, { target: { value: `${"d".repeat(255)}  ` } });
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ name: "Night Watchman", description: "d".repeat(255) });
  });

  it("normalises each dotted permission code into one control id", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openNewRoleDialog(user);

    // Each label points at a dot-free control id and names its own checkbox,
    // so the pairing survives id-based lookups and CSS selectors.
    expect(within(dialog).getByText("View animals")).toHaveAttribute(
      "for",
      "permission-animals-view",
    );
    expect(
      within(dialog).getByRole("checkbox", { name: "View animals" }),
    ).toHaveAttribute("aria-labelledby", "permission-animals-view-label");
    expect(within(dialog).getByText("Complete duties")).toHaveAttribute(
      "for",
      "permission-tasks-complete",
    );
  });

  it("selects the view permission that each module action depends on", async () => {
    const dependencies = [
      ["Move animals", "View animals"],
      ["Record weights", "View animals"],
      ["Change animal status", "View animals"],
      ["Assign duties", "View assigned duties"],
      ["Complete duties", "View assigned duties"],
      ["Verify duties", "View assigned duties"],
    ];
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openNewRoleDialog(user);

    for (const [action, view] of dependencies) {
      const actionBox = within(dialog).getByRole("checkbox", { name: action });
      const viewBox = within(dialog).getByRole("checkbox", { name: view });

      await user.click(actionBox);
      expect(viewBox).toBeChecked();
      // …and it stays locked while the action still needs it.
      expect(viewBox).toHaveAttribute("aria-disabled", "true");

      // Reset for the next pair: the view unlocks once nothing requires it.
      await user.click(actionBox);
      await user.click(viewBox);
      expect(viewBox).not.toBeChecked();
    }
  });

  it("names every action that keeps a view permission selected", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openNewRoleDialog(user);

    await user.click(within(dialog).getByRole("checkbox", { name: "Add animals" }));
    await user.click(within(dialog).getByRole("checkbox", { name: "Move animals" }));

    expect(
      within(dialog).getByText("Required by Add animals, Move animals."),
    ).toBeInTheDocument();
  });

  it("labels a failed create, clears it on retry and toasts the created role", async () => {
    const { gate, release } = createGate();
    server.use(
      http.post("/api/team/roles", async () => {
        postCalls += 1;
        if (postCalls === 1) {
          return HttpResponse.json({ detail: "a role with that name exists" }, { status: 400 });
        }
        await gate;
        return HttpResponse.json({ ...ROLE_HELPER, id: 14 }, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openNewRoleDialog(user);
    await user.type(within(dialog).getByLabelText(/Role name/), "Night Watchman");
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "a role with that name exists",
    );
    expect(toastMock.error).toHaveBeenCalledWith("a role with that name exists");

    await user.click(within(dialog).getByRole("button", { name: "Retry create role" }));

    expect(await within(dialog).findByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(within(dialog).queryByText("a role with that name exists")).not.toBeInTheDocument();
    release();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Role created."));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("labels a failed save of an existing role and toasts the saved one", async () => {
    let putCalls = 0;
    server.use(
      http.put("/api/team/roles/:roleId", async () => {
        putCalls += 1;
        if (putCalls === 1) {
          return HttpResponse.json({ detail: "role is locked by an import" }, { status: 400 });
        }
        return HttpResponse.json(ROLE_NIGHT_WATCH);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(roleCard("Night Watch")).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "role is locked by an import",
    );
    expect(toastMock.error).toHaveBeenCalledWith("role is locked by an import");

    await user.click(within(dialog).getByRole("button", { name: "Retry save role" }));

    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Role saved."));
  });
});

describe("TeamPage role dialog copy for a delegated team.manage holder", () => {
  const ROLE_VIEWER = {
    id: 20,
    code: null,
    name: "Animal viewer",
    description: null,
    permissions: ["animals.view"],
    revision: 1,
    member_count: 0,
  };
  const ROLE_MANAGER = {
    id: 21,
    code: null,
    name: "Team manager",
    description: null,
    permissions: ["team.manage"],
    revision: 1,
    member_count: 0,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      permissionsHandler(["team.manage", "animals.view"]),
      teamHandler({
        ...TEAM_PAYLOAD,
        memberships: [MEMBER_SELF],
        roles: [ROLE_VIEWER, ROLE_MANAGER],
      }),
    );
  });

  it("explains which permissions the editor may not grant", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TeamPage />);
    expect(await screen.findByText(ROLE_VIEWER.name)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "New role" })).toBeEnabled());
    const dialog = await openNewRoleDialog(user);

    // Held, but reserved to the owner.
    expect(
      within(dialog).getByText("(only the farm owner can grant this)"),
    ).toBeInTheDocument();
    // Simply not held by this editor.
    expect(within(dialog).getAllByText("(you cannot grant this)").length).toBeGreaterThan(0);
    expect(
      within(dialog).getByRole("checkbox", { name: "Manage team, roles & passwords" }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("edits a role inside the ceiling and explains the team-manager role it cannot", async () => {
    renderWithProviders(<TeamPage />);
    expect(await screen.findByText(ROLE_MANAGER.name)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "New role" })).toBeEnabled());

    const withinCeiling = within(roleCard(ROLE_VIEWER.name)).getByRole("button", {
      name: "Edit",
    });
    expect(withinCeiling).toBeEnabled();
    expect(withinCeiling).not.toHaveAttribute("title");

    const managerEdit = within(roleCard(ROLE_MANAGER.name)).getByRole("button", { name: "Edit" });
    expect(managerEdit).toBeDisabled();
    expect(managerEdit).toHaveAttribute(
      "title",
      "Only the farm owner can edit or delete a team-manager role.",
    );
  });
});

describe("TeamPage role cards copy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server.use(teamHandler());
  });

  it("explains why a role cannot be deleted", async () => {
    await renderLoaded();

    expect(within(roleCard("Manager")).getByRole("button", { name: "Delete" })).toHaveAttribute(
      "title",
      "Preset roles can't be deleted.",
    );
    expect(
      within(roleCard("Night Watch")).getByRole("button", { name: "Delete" }),
    ).toHaveAttribute("title", "Role still has workers assigned — reassign them first.");
    expect(
      within(roleCard("Helper")).getByRole("button", { name: "Delete" }),
    ).not.toHaveAttribute("title");
  });

  it("separates a preset role's name from its badge", async () => {
    await renderLoaded();

    const card = roleCard("Manager");
    // The badge reads as the semantic warning variant, not a raw palette tint.
    const badge = within(card).getByText("Preset").closest("[data-slot=badge]");
    expect(badge).toHaveAttribute("data-variant", "secondary");
    expect(within(card).getByText("Manager").parentElement).toHaveTextContent("Manager Preset");
  });

  it("toasts a deleted role and a rejected deletion", async () => {
    let deleteCalls = 0;
    server.use(
      http.delete("/api/team/roles/:roleId", () => {
        deleteCalls += 1;
        return deleteCalls === 1
          ? HttpResponse.json({ detail: "role is referenced by an invite" }, { status: 409 })
          : new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const card = roleCard("Helper");

    await user.click(within(card).getByRole("button", { name: "Delete" }));
    const confirmation = await screen.findByRole("dialog");
    await user.click(within(confirmation).getByRole("button", { name: "Delete role" }));

    expect(await within(card).findByRole("alert")).toHaveTextContent(
      "role is referenced by an invite",
    );
    expect(toastMock.error).toHaveBeenCalledWith("role is referenced by an invite");

    await user.click(within(card).getByRole("button", { name: "Retry delete role" }));

    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Role deleted."));
  });
});

describe("TeamPage page-level copy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("describes the workers card for the farm owner", async () => {
    server.use(teamHandler());
    await renderLoaded();

    expect(screen.getByText("People who can sign in to this farm.")).toBeInTheDocument();
  });

  it("falls back to a generic message when the team request fails without a detail", async () => {
    server.use(http.get("/api/team", () => HttpResponse.error()));
    renderWithProviders(<TeamPage />);

    expect(await screen.findByText("Could not load the team.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry team" })).toBeInTheDocument();
  });

  it("waits for the permission check instead of denying access", async () => {
    const { gate, release } = createGate();
    server.use(
      teamHandler(),
      http.get("/api/auth/permissions", async () => {
        await gate;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );
    renderWithProviders(<TeamPage />);

    await waitFor(() => {
      expect(screen.getByText("Loading…")).toBeInTheDocument();
      expect(screen.queryByText(/have access to this page/)).not.toBeInTheDocument();
    });

    release();
    expect((await screen.findAllByText(MEMBER_RAVI.email)).length).toBeGreaterThan(0);
  });
});

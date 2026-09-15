/**
 * Mutation-hardening tests for the team page: targeted at Stryker survivors —
 * invalidation scope, activate/deactivate branching, reset-password gating and
 * lock release, payload trims, permission-dialog copy and dependency repair,
 * role-card copy, protected-target rows, empty-state owner gates, canStart
 * guards and the permissions/team error branches.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

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
  // jsdom lacks the pointer-capture/scroll APIs Radix Select relies on.
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
const ROLE_ANIMAL = {
  id: 12,
  code: null,
  name: "Animal keeper",
  description: null,
  permissions: ["animals.view", "animals.create"],
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
const MEMBER_RAVI = {
  id: 2,
  user_id: 22,
  email: "ravi@example.com",
  name: "Ravi Kumar",
  role_id: 11,
  role_name: "Helper",
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
const MEMBER_MEENA = {
  id: 4,
  user_id: 24,
  email: "meena@example.com",
  name: "Meena",
  role_id: 12,
  role_name: "Animal keeper",
  is_active: true,
  can_reset_password: true,
  reset_password_block_reason: null,
};

const TEAM_PAYLOAD = {
  memberships: [MEMBER_SELF, MEMBER_RAVI, MEMBER_SITA, MEMBER_MEENA],
  roles: [ROLE_HELPER, ROLE_ANIMAL, ROLE_PRESET],
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
  // Worker content also renders in the below-md card list (md:hidden) — scope
  // to the desktop table so duplicated text stays unambiguous.
  const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
  const row = within(table).getByText(email).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

async function renderLoaded() {
  const rendered = renderWithProviders(<TeamPage />);
  expect((await screen.findAllByText(MEMBER_RAVI.email)).length).toBeGreaterThan(0);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "New role" })).toBeEnabled(),
  );
  return rendered;
}

describe("TeamPage mutation hardening — invalidation scope", () => {
  it("a team action refreshes team + permissions but leaves unrelated queries untouched", async () => {
    let getCalls = 0;
    let permCalls = 0;
    server.use(
      http.get("/api/team", () => {
        getCalls += 1;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.get("/api/auth/permissions", () => {
        permCalls += 1;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
      http.post("/api/team/workers/:membershipId/role", () =>
        HttpResponse.json({ ...MEMBER_RAVI, role_id: ROLE_HELPER.id }),
      ),
    );
    const user = userEvent.setup();
    const { queryClient } = await renderLoaded();
    queryClient.setQueryData(["unrelated", "probe"], 1);
    const permsBefore = permCalls;
    const teamBefore = getCalls;

    await user.click(within(workerRow(MEMBER_RAVI.email)).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Animal keeper" }));

    await waitFor(() => expect(getCalls).toBeGreaterThan(teamBefore));
    await waitFor(() => expect(permCalls).toBeGreaterThan(permsBefore));
    // The invalidation must stay scoped: a blanket invalidateQueries({}) would
    // mark every cached query stale.
    expect(queryClient.getQueryState(["unrelated", "probe"])?.isInvalidated).toBe(false);
  });
});

describe("TeamPage mutation hardening — worker status actions", () => {
  beforeEach(() => {
    server.use(teamHandler());
  });

  it("activating an inactive worker PUTs immediately, without a confirmation dialog", async () => {
    let putBody: Record<string, unknown> | null = null;
    server.use(
      http.put("/api/team/workers/:membershipId/status", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...MEMBER_SITA, is_active: true });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(
      within(workerRow(MEMBER_SITA.email)).getByRole("button", { name: "Activate" }),
    );

    await waitFor(() => expect(putBody).toEqual({ is_active: true }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("the deactivation dialog's Cancel closes it without any request", async () => {
    let puts = 0;
    server.use(
      http.put("/api/team/workers/:membershipId/status", () => {
        puts += 1;
        return HttpResponse.json({ ...MEMBER_RAVI, is_active: false });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Deactivate" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(screen.getByText("Deactivate Ravi Kumar?")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(puts).toBe(0);
  });

  it("retries a failed role change by POSTing the same role again", async () => {
    let calls = 0;
    let lastBody: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/team/workers/:membershipId/role", async ({ request }) => {
        calls += 1;
        lastBody = (await request.json()) as Record<string, unknown>;
        if (calls === 1) {
          return HttpResponse.json({ detail: "role assignment conflict" }, { status: 409 });
        }
        return HttpResponse.json({ ...MEMBER_RAVI, role_id: ROLE_ANIMAL.id });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);

    await user.click(within(row).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Animal keeper" }));

    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "role assignment conflict",
    );
    await user.click(within(row).getByRole("button", { name: "Retry role change" }));

    await waitFor(() => expect(calls).toBe(2));
    expect(lastBody).toEqual({ role_id: ROLE_ANIMAL.id });
  });
});

describe("TeamPage mutation hardening — reset password gating", () => {
  beforeEach(() => {
    server.use(teamHandler());
  });

  it("an ineligible target never opens the reset dialog, even via an imperative click", async () => {
    server.use(
      teamHandler({
        ...TEAM_PAYLOAD,
        memberships: [
          MEMBER_SELF,
          {
            ...MEMBER_RAVI,
            can_reset_password: false,
            reset_password_block_reason: "reactivate the membership first",
          },
        ],
      }),
    );
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);
    const reset = within(row).getByRole("button", { name: "Reset password" });
    expect(reset).toBeDisabled();
    expect(within(row).getByText("reactivate the membership first")).toBeInTheDocument();

    fireEvent.click(reset);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancelling an open reset dialog releases the row lock", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const row = workerRow(MEMBER_RAVI.email);
    const deactivate = within(row).getByRole("button", { name: "Deactivate" });

    await user.click(within(row).getByRole("button", { name: "Reset password" }));
    const dialog = await screen.findByRole("dialog");
    expect(deactivate).toBeDisabled();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(deactivate).toBeEnabled());
  });
});

describe("TeamPage mutation hardening — add worker dialog", () => {
  let postBody: Record<string, unknown> | null;

  beforeEach(() => {
    postBody = null;
    server.use(
      teamHandler(),
      http.post("/api/team/workers", async ({ request }) => {
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

  it("trims the email and name in the payload (empty name stays null)", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Email/), "  new@example.com ");
    await user.type(within(dialog).getByLabelText(/^Name/), "  New Worker  ");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /Helper/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ email: "new@example.com", name: "New Worker" });
  });

  it("a whitespace-only name is submitted as null", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.type(within(dialog).getByLabelText(/^Name/), "   ");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /Helper/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ name: null });
  });

  it("Cancel closes an idle dialog", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("Cancel stays disabled while the POST is in flight", async () => {
    let release!: () => void;
    let started!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const startedPromise = new Promise<void>((resolve) => {
      started = resolve;
    });
    server.use(
      http.post("/api/team/workers", async () => {
        started();
        await gate;
        return HttpResponse.json(MEMBER_RAVI, { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.type(within(dialog).getByLabelText(/Password/), "newworker123");
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /Helper/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add worker" }));
    await startedPromise;

    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Adding…" })).toBeDisabled();

    await act(async () => {
      release();
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("TeamPage mutation hardening — reset dialog flight state", () => {
  it("Cancel is disabled while the reset POST is in flight", async () => {
    let release!: () => void;
    let started!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const startedPromise = new Promise<void>((resolve) => {
      started = resolve;
    });
    server.use(
      teamHandler(),
      http.post("/api/team/workers/:membershipId/reset-password", async () => {
        started();
        await gate;
        return HttpResponse.json({ ok: true });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Reset password" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/New password/), "brandnewpass");
    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));
    await startedPromise;

    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Resetting…" })).toBeDisabled();

    await act(async () => {
      release();
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("TeamPage mutation hardening — role dialog", () => {
  let postBody: Record<string, unknown> | null;

  beforeEach(() => {
    postBody = null;
    server.use(
      teamHandler(),
      http.post("/api/team/roles", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(ROLE_HELPER, { status: 201 });
      }),
    );
  });

  async function openNewRole() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New role" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("ticking a plain view permission adds exactly that permission (no undefined entry)", async () => {
    const { user, dialog } = await openNewRole();
    await user.type(within(dialog).getByLabelText(/Role name/), "Viewer");
    await user.click(within(dialog).getByRole("checkbox", { name: "View animals" }));
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toEqual({
      name: "Viewer",
      description: null,
      permissions: ["animals.view"],
    });
  });

  it("ticking an action still pulls in its view dependency", async () => {
    const { user, dialog } = await openNewRole();
    await user.type(within(dialog).getByLabelText(/Role name/), "Entry");
    await user.click(within(dialog).getByRole("checkbox", { name: "Add animals" }));
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      permissions: ["animals.create", "animals.view"],
    });
  });

  it("trims the role name and blanks the description", async () => {
    const { user, dialog } = await openNewRole();
    await user.type(within(dialog).getByLabelText(/Role name/), "  Watchman  ");
    await user.type(within(dialog).getByLabelText(/Description/), "   ");
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ name: "Watchman", description: null });
  });

  it("explains per-code why a non-owner cannot grant each permission", async () => {
    server.use(
      teamHandler(),
      permissionsHandler(["team.manage"]),
      http.post("/api/team/roles", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(ROLE_HELPER, { status: 201 });
      }),
    );
    const { dialog } = await openNewRole();

    expect(
      within(dialog).getByText("(only the farm owner can grant this)"),
    ).toBeInTheDocument();
    expect(within(dialog).getAllByText("(you cannot grant this)").length).toBeGreaterThan(
      0,
    );
  });
});

describe("TeamPage mutation hardening — role cards", () => {
  beforeEach(() => {
    server.use(teamHandler());
  });

  function cardOf(name: string): HTMLElement {
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

  it("singularises and pluralises the member count exactly", async () => {
    await renderLoaded();

    expect(within(cardOf("Helper")).getByText("1 member")).toBeInTheDocument();
    expect(within(cardOf("Animal keeper")).getByText("0 members")).toBeInTheDocument();
  });

  it("only preset roles carry the built-in suffix", async () => {
    await renderLoaded();

    expect(
      within(cardOf("Manager")).getByText(/built in — edits allowed, deletion not/),
    ).toBeInTheDocument();
    expect(
      within(cardOf("Helper")).queryByText(/built in — edits allowed, deletion not/),
    ).not.toBeInTheDocument();
  });
});

describe("TeamPage mutation hardening — RBAC row and page gates", () => {
  it("a non-owner cannot manage a worker whose role exceeds their ceiling", async () => {
    server.use(
      permissionsHandler(["team.manage", "animals.view"]),
      teamHandler({
        ...TEAM_PAYLOAD,
        // Keep only self, Meena (role above ceiling) and Ravi (manageable).
        memberships: [MEMBER_SELF, MEMBER_RAVI, MEMBER_MEENA],
      }),
    );
    await renderLoaded();

    const meena = workerRow(MEMBER_MEENA.email);
    expect(
      within(meena).getByText(
        "You can only manage workers whose current role stays within your own permissions.",
      ),
    ).toBeInTheDocument();
    expect(within(meena).queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();
    expect(within(meena).queryByRole("button", { name: "Reset password" })).not.toBeInTheDocument();
    // A worker inside the ceiling stays fully manageable.
    expect(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Deactivate" }),
    ).toBeInTheDocument();
  });

  it("an owner sees the empty-state Add worker; a delegated admin sees none", async () => {
    const emptyPayload = {
      ...TEAM_PAYLOAD,
      memberships: [],
    };
    server.use(teamHandler(emptyPayload));
    const ownerView = renderWithProviders(<TeamPage />);
    expect(await screen.findAllByRole("button", { name: "Add worker" })).toHaveLength(2);
    ownerView.unmount();

    server.use(
      permissionsHandler(["team.manage"]),
      teamHandler(emptyPayload),
    );
    renderWithProviders(<TeamPage />);
    expect(await screen.findByText(/No workers yet/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Add worker" }),
    ).not.toBeInTheDocument();
  });

  it("imperative clicks on Add worker are refused while the snapshot refreshes", async () => {
    let getCalls = 0;
    let release!: () => void;
    let announce!: () => void;
    const started = new Promise<void>((resolve) => {
      announce = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/team", async () => {
        getCalls += 1;
        if (getCalls === 1) return HttpResponse.json(TEAM_PAYLOAD);
        announce();
        await gate;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
    );
    const { queryClient } = await renderLoaded();
    const addWorker = screen.getByRole("button", { name: "Add worker" });

    let refresh!: Promise<void>;
    act(() => {
      refresh = queryClient.invalidateQueries({ queryKey: getTeamPageApiTeamGetQueryKey() });
    });
    await started;
    await waitFor(() => expect(addWorker).toBeDisabled());

    fireEvent.click(addWorker);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await act(async () => {
      release();
      await refresh;
    });
    await waitFor(() => expect(addWorker).toBeEnabled());
  });
});

describe("TeamPage mutation hardening — error branches", () => {
  it("recovers from a permissions failure through the in-place retry", async () => {
    let permCalls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        permCalls += 1;
        if (permCalls === 1) {
          return HttpResponse.json({ detail: "permissions down" }, { status: 500 });
        }
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
      teamHandler(),
    );
    const user = userEvent.setup();
    renderWithProviders(<TeamPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load your permissions");
    await user.click(screen.getByRole("button", { name: "Retry permissions" }));

    expect((await screen.findAllByText(MEMBER_RAVI.email)).length).toBeGreaterThan(0);
  });

  it("shows the team load error and retries into the page", async () => {
    let getCalls = 0;
    server.use(
      http.get("/api/team", () => {
        getCalls += 1;
        if (getCalls === 1) {
          return HttpResponse.json({ detail: "database unavailable" }, { status: 500 });
        }
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<TeamPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("database unavailable");
    await user.click(screen.getByRole("button", { name: "Retry team" }));

    expect((await screen.findAllByText(MEMBER_RAVI.email)).length).toBeGreaterThan(0);
  });
});

describe("TeamPage round-2 mutation survivors", () => {

  it("keeps a row's actions locked until the post-write snapshot refetch lands", async () => {
    let rolePosts = 0;
    let getCalls = 0;
    let releaseRefetch: (() => void) | undefined;
    server.use(
      http.get("/api/team", () => {
        getCalls += 1;
        if (getCalls === 1) return HttpResponse.json(TEAM_PAYLOAD);
        return new Promise<Response>((resolve) => {
          releaseRefetch = () => resolve(HttpResponse.json(TEAM_PAYLOAD));
        });
      }),
      http.post("/api/team/workers/:membershipId/role", () => {
        rolePosts += 1;
        return HttpResponse.json(ROLE_ANIMAL);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    const row = workerRow(MEMBER_RAVI.email);
    await user.click(within(row).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Animal keeper" }));
    await waitFor(() => expect(rolePosts).toBe(1));

    // The POST answered but the authoritative snapshot has not: the row stays
    // claimed (select and reset locked) until that refetch settles.
    await waitFor(() =>
      expect(within(workerRow(MEMBER_RAVI.email)).getByRole("combobox")).toBeDisabled(),
    );
    expect(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Reset password" }),
    ).toBeDisabled();

    releaseRefetch?.();
    await waitFor(() =>
      expect(within(workerRow(MEMBER_RAVI.email)).getByRole("combobox")).toBeEnabled(),
    );
  });

  it("clears a half-typed worker draft when Cancel closes the dialog", async () => {
    server.use(teamHandler());
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Add worker" }));
    let dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/email/i), "draft@example.com");
    await user.type(within(dialog).getByLabelText(/password/i), "secret123");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Add worker" }));
    dialog = await screen.findByRole("dialog");
    expect((within(dialog).getByLabelText(/email/i) as HTMLInputElement).value).toBe("");
    expect((within(dialog).getByLabelText(/password/i) as HTMLInputElement).value).toBe("");
  });

  it("starts the reset-password dialog with an empty password field", async () => {
    server.use(teamHandler());
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(
      within(workerRow(MEMBER_RAVI.email)).getByRole("button", { name: "Reset password" }),
    );
    const dialog = await screen.findByRole("dialog");
    const password = within(dialog).getByLabelText(/new password/i);
    expect((password as HTMLInputElement).value).toBe("");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  });


});

describe("TeamPage round-2 mutation survivors (role ceiling)", () => {
  it("offers a delegated admin only the roles within their ceiling in row selects", async () => {
    server.use(teamHandler(), permissionsHandler(["team.manage"]));
    const user = userEvent.setup();
    await renderLoaded();

    // Ravi holds Helper (no permissions) — editable by this admin; the
    // Animal keeper role carries permissions the admin lacks, and preset
    // roles are owner-only, so only Helper may be offered.
    await user.click(within(workerRow(MEMBER_RAVI.email)).getByRole("combobox"));
    const options = await screen.findAllByRole("option");
    const names = options.map((option) => option.textContent);
    expect(names).toEqual(["Helper"]);
  });
});

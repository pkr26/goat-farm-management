/**
 * Team page role dialog permission safety: a user holding only team.manage
 * sees the full matrix, cannot grant unheld permissions, and cannot manage
 * roles above their own effective-permission ceiling.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import TeamPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/team",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

/** Mirrors the real TeamOut shape (subset of the backend's PERMISSION_GROUPS). */
const TEAM_PAYLOAD = {
  memberships: [
    {
      id: 1,
      user_id: TEST_USER.id,
      email: TEST_USER.email,
      name: TEST_USER.name,
      role_id: null,
      role_name: null,
      is_active: true,
      can_reset_password: false,
      reset_password_block_reason: "This account must use self-service password recovery.",
    },
  ],
  roles: [],
  permission_groups: [
    { group: "Animals", codes: ["animals.view", "animals.create"] },
    { group: "Tasks / duties", codes: ["tasks.view", "tasks.complete"] },
    { group: "Team", codes: ["team.manage"] },
  ],
  permission_labels: {
    "animals.view": "View animals",
    "animals.create": "Add animals",
    "tasks.view": "View assigned duties",
    "tasks.complete": "Complete duties",
    "team.manage": "Manage team, roles & passwords",
  },
};

describe("TeamPage role dialog clamping (editor holds only team.manage)", () => {
  let rolePostBody: Record<string, unknown> | null;

  beforeEach(() => {
    rolePostBody = null;
    server.use(
      permissionsHandler(["team.manage"]),
      http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)),
      http.post("/api/team/roles", async ({ request }) => {
        rolePostBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            id: 9,
            code: null,
            name: String(rolePostBody.name),
            description: null,
            permissions: rolePostBody.permissions,
          },
          { status: 201 },
        );
      }),
    );
  });

  async function openRoleDialog() {
    const user = userEvent.setup();
    renderWithProviders(<TeamPage />);
    // Membership row proves permissions + team payload resolved.
    expect((await screen.findAllByText(TEST_USER.email)).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "New role" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("shows the full matrix but disables permissions the editor cannot grant", async () => {
    const { dialog } = await openRoleDialog();

    expect(within(dialog).getByText("Animals")).toBeInTheDocument();
    expect(within(dialog).getByText("Tasks / duties")).toBeInTheDocument();
    expect(within(dialog).getAllByRole("checkbox")).toHaveLength(5);
    expect(within(dialog).getByRole("checkbox", { name: "View animals" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(within(dialog).getByRole("checkbox", { name: "Add animals" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(
      within(dialog).getByRole("checkbox", { name: "Manage team, roles & passwords" }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("does not let a non-owner grant team.manage even when they hold it", async () => {
    const { user, dialog } = await openRoleDialog();

    await user.type(within(dialog).getByLabelText(/role name/i), "Night Watch");
    expect(
      within(dialog).getByRole("checkbox", { name: "Manage team, roles & passwords" }),
    ).toHaveAttribute("aria-disabled", "true");
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    await waitFor(() => expect(rolePostBody).not.toBeNull());
    expect(rolePostBody).toEqual({
      name: "Night Watch",
      description: null,
      permissions: [],
    });
  });

  it("only offers worker roles inside the delegated editor's permission ceiling", async () => {
    const safeRole = {
      id: 10,
      code: null,
      name: "Unprivileged helper",
      description: null,
      permissions: [],
      revision: 1,
      member_count: 1,
    };
    const animalRole = {
      id: 11,
      code: null,
      name: "Animal administrator",
      description: null,
      permissions: ["animals.view", "animals.create"],
      revision: 1,
      member_count: 0,
    };
    const managerRole = {
      id: 12,
      code: null,
      name: "Team manager",
      description: null,
      permissions: ["team.manage"],
      revision: 1,
      member_count: 0,
    };
    server.use(
      http.get("/api/team", () =>
        HttpResponse.json({
          ...TEAM_PAYLOAD,
          memberships: [
            ...TEAM_PAYLOAD.memberships,
            {
              id: 2,
              user_id: 2,
              email: "worker@example.com",
              name: "Worker",
              role_id: safeRole.id,
              role_name: safeRole.name,
              is_active: true,
              can_reset_password: false,
              reset_password_block_reason: null,
            },
          ],
          roles: [safeRole, animalRole, managerRole],
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TeamPage />);
    await screen.findAllByText("worker@example.com");
    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const row = within(table).getByText("worker@example.com").closest("tr") as HTMLElement;
    await waitFor(() => expect(screen.getByRole("button", { name: "New role" })).toBeEnabled());

    await user.click(within(row).getByRole("combobox"));

    expect(await screen.findByRole("option", { name: safeRole.name })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: animalRole.name })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: managerRole.name })).not.toBeInTheDocument();
  });

  it("disables editing a role whose permissions exceed the editor's ceiling", async () => {
    const existingRole = {
      id: 12,
      code: null,
      name: "Animal keeper",
      description: null,
      permissions: ["animals.view", "animals.create"],
      revision: 1,
      member_count: 0,
    };
    server.use(
      http.get("/api/team", () =>
        HttpResponse.json({ ...TEAM_PAYLOAD, roles: [existingRole] }),
      ),
    );
    renderWithProviders(<TeamPage />);
    expect(await screen.findByText("Animal keeper")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "New role" })).toBeEnabled());
    const edit = screen.getByRole("button", { name: "Edit" });
    expect(edit).toBeDisabled();
    expect(edit).toHaveAttribute(
      "title",
      "You can only edit or delete roles whose permissions you also hold.",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

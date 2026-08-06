/**
 * Team page role dialog permission clamping: a user holding only
 * team.manage sees exactly the team.* checkbox in the permission matrix —
 * groups they hold nothing in are hidden entirely — and a created role can
 * only carry the held permission.
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
    expect(await screen.findByText(TEST_USER.email)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New role" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("shows only the team.* checkbox; unheld groups are hidden entirely", async () => {
    const { dialog } = await openRoleDialog();

    // The single grantable permission is rendered with its label.
    expect(within(dialog).getByText("Manage team, roles & passwords")).toBeInTheDocument();
    expect(within(dialog).getAllByRole("checkbox")).toHaveLength(1);

    // Groups the editor holds nothing in do not render at all.
    expect(within(dialog).queryByText("Animals")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Tasks / duties")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("View animals")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Add animals")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Complete duties")).not.toBeInTheDocument();
  });

  it("a created role carries only the held team.manage permission", async () => {
    const { user, dialog } = await openRoleDialog();

    await user.type(within(dialog).getByLabelText(/role name/i), "Night Watch");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    await waitFor(() => expect(rolePostBody).not.toBeNull());
    expect(rolePostBody).toEqual({
      name: "Night Watch",
      description: null,
      permissions: ["team.manage"],
    });
  });
});

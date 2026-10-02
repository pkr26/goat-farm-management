/**
 * Team page i18n: with the language set to te the page chrome (header,
 * workers card, role cards) and the add-worker dialog render fully in
 * Telugu — field labels, the member-count plural split and the zod
 * validation messages all resolve through the team.* catalog keys.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import TeamPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/team",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

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
  roles: [
    {
      id: 10,
      code: null,
      name: "Night Watch",
      description: null,
      permissions: ["animals.view"],
      revision: 1,
      member_count: 1,
    },
    {
      id: 13,
      code: "MANAGER",
      name: "Manager",
      description: "preset manager role",
      permissions: ["team.manage"],
      revision: 3,
      member_count: 3,
    },
  ],
  permission_groups: [
    { group: "Animals", codes: ["animals.view"] },
    { group: "Team", codes: ["team.manage"] },
  ],
  permission_labels: {
    "animals.view": "View animals",
    "team.manage": "Manage team, roles & passwords",
  },
};

beforeEach(() => {
  server.use(http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)));
});

async function renderTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  renderWithProviders(
    <LanguageProvider>
      <TeamPage />
    </LanguageProvider>,
  );
  // The loaded page proves itself through the workers card heading.
  await screen.findByText("కార్మికులు");
  return { user };
}

describe("TeamPage — Telugu", () => {
  it("renders the membership status chips through the enum labels, in Telugu too (2026-10-01 audit, 06-2)", async () => {
    server.use(
      http.get("/api/team", () =>
        HttpResponse.json({
          ...TEAM_PAYLOAD,
          memberships: [
            ...TEAM_PAYLOAD.memberships,
            {
              id: 2,
              user_id: 77,
              email: "ravi@goatfarm.test",
              name: "Ravi",
              role_id: 10,
              role_name: "Night Watch",
              is_active: false,
              can_reset_password: true,
              reset_password_block_reason: null,
            },
          ],
        }),
      ),
    );
    await renderTelugu();

    // Both the desktop row and the below-md card resolve the chips through
    // the status family — the badge's humanize fallback used to render
    // English-only "Active"/"Inactive".
    expect(screen.getAllByText("సక్రియం").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("క్రియారహితం").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("Active")).not.toBeInTheDocument();
    expect(screen.queryByText("Inactive")).not.toBeInTheDocument();
  });

  it("renders the page chrome and role cards in Telugu, including the member-count plural split", async () => {
    await renderTelugu();

    expect(screen.getByRole("heading", { name: "బృందం" })).toBeInTheDocument();
    expect(screen.getByText("పాత్రలు")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "కొత్త పాత్ర" })).toBeInTheDocument();
    // Preset badge, one/many member counts and the built-in suffix.
    expect(screen.getByText("ప్రీసెట్")).toBeInTheDocument();
    expect(screen.getByText("1 మంది సభ్యుడు")).toBeInTheDocument();
    expect(screen.getByText(/3 మంది సభ్యులు/)).toBeInTheDocument();
    expect(
      screen.getByText(/అంతర్నిర్మితం — సవరణలు అనుమతించబడతాయి, తొలగింపు కాదు/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/members/)).not.toBeInTheDocument();
  });

  it("renders the add-worker dialog and its zod validation messages in Telugu", async () => {
    const { user } = await renderTelugu();

    await user.click(screen.getByRole("button", { name: "కార్మికుడిని చేర్చండి" }));
    const dialog = await screen.findByRole("dialog", { name: "కార్మికుడిని చేర్చండి" });

    expect(within(dialog).getByLabelText("పేరు")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("ఇమెయిల్ *")).toBeInTheDocument();
    expect(
      within(dialog).getByLabelText("పాస్‌వర్డ్ (కనీసం 12 అక్షరాలు) *"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("పాత్ర *")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "కార్మికుడిని చేర్చండి" }));

    const emailError = await within(dialog).findByText(
      "సరైన ఇమెయిల్ చిరునామా నమోదు చేయండి",
      { selector: "p[role='alert']" },
    );
    expect(emailError).toHaveAttribute("id", "worker-email-error");
    expect(
      within(dialog).getByText("పాస్‌వర్డ్ కనీసం 12 అక్షరాలు ఉండాలి"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("పాత్రను ఎంచుకోండి")).toBeInTheDocument();
    expect(within(dialog).queryByText("Pick a role")).not.toBeInTheDocument();
  });

  it("keeps the Telugu catalog at parity for the page keys", () => {
    expect(translate("te", "team.validation.passwordMin")).toBe(
      "పాస్‌వర్డ్ కనీసం 12 అక్షరాలు ఉండాలి",
    );
    expect(translate("te", "team.roles.memberCount_one", { count: 1 })).toBe("1 మంది సభ్యుడు");
    expect(translate("te", "team.roles.memberCount_many", { count: 3 })).toBe("3 మంది సభ్యులు");
    expect(translate("te", "team.deactivate.title", { name: "Ravi Kumar" })).toBe(
      "Ravi Kumarను డీయాక్టివేట్ చేయాలా?",
    );
    expect(translate("te", "team.toast.workerAdded")).toBe("కార్మికుడు చేరాడు.");
    // English stays the fallback source of truth.
    expect(translate("en", "team.roles.memberCount_many", { count: 3 })).toBe("3 members");
  });
});

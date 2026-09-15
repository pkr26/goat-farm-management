import { act, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import TeamPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/team",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

// This focused adapter keeps Select's public contract while making two value
// changes deliverable in one React task. Base UI closes its popup after the
// first pick, which otherwise makes the row's synchronous lock unreachable
// from a second same-render event in jsdom.
vi.mock("@/components/ui/select", async () => {
  const React = await import("react");
  const SelectContext = React.createContext<{
    disabled: boolean;
    onValueChange?: (value: string) => void;
  }>({ disabled: false });

  function Select({
    children,
    disabled = false,
    onValueChange,
  }: {
    children: ReactNode;
    disabled?: boolean;
    onValueChange?: (value: string) => void;
    value?: string;
    items?: Record<string, string>;
  }) {
    return (
      <SelectContext.Provider value={{ disabled, onValueChange }}>
        <div>{children}</div>
      </SelectContext.Provider>
    );
  }

  function SelectTrigger(
    props: ButtonHTMLAttributes<HTMLButtonElement> & { size?: string },
  ) {
    const { disabled } = React.useContext(SelectContext);
    return (
      <button
        type="button"
        role="combobox"
        id={props.id}
        className={props.className}
        aria-label={props["aria-label"]}
        aria-controls="mock-role-options"
        aria-expanded="true"
        disabled={disabled}
      >
        {props.children}
      </button>
    );
  }

  function SelectItem({
    children,
    value,
    disabled = false,
  }: {
    children: ReactNode;
    value: string;
    disabled?: boolean;
  }) {
    const select = React.useContext(SelectContext);
    return (
      <button
        type="button"
        role="option"
        aria-selected="false"
        disabled={disabled || select.disabled}
        onClick={() => select.onValueChange?.(value)}
      >
        {children}
      </button>
    );
  }

  return {
    Select,
    SelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    SelectItem,
    SelectTrigger,
    SelectValue: () => null,
  };
});

afterEach(() => {
  vi.restoreAllMocks();
});

const CURRENT_ROLE = {
  id: 10,
  code: null,
  name: "Current role",
  description: null,
  permissions: [],
  revision: 1,
  member_count: 1,
};

const HELPER_ROLE = {
  ...CURRENT_ROLE,
  id: 11,
  name: "Helper role",
  member_count: 0,
};

const RELIEF_ROLE = {
  ...CURRENT_ROLE,
  id: 12,
  name: "Relief role",
  member_count: 0,
};

const WORKER = {
  id: 2,
  user_id: 22,
  email: "worker@example.com",
  name: "Worker",
  role_id: CURRENT_ROLE.id,
  role_name: CURRENT_ROLE.name,
  is_active: true,
  can_reset_password: true,
  reset_password_block_reason: null,
};

const TEAM_PAYLOAD = {
  memberships: [WORKER],
  roles: [CURRENT_ROLE, HELPER_ROLE, RELIEF_ROLE],
  permission_groups: [],
  permission_labels: {},
};

it("single-flights two role choices delivered before pending state paints", async () => {
  let calls = 0;
  let releaseRole!: () => void;
  let markRoleStarted!: () => void;
  const roleStarted = new Promise<void>((resolve) => {
    markRoleStarted = resolve;
  });
  const roleGate = new Promise<void>((resolve) => {
    releaseRole = resolve;
  });
  server.use(
    http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)),
    http.post("/api/team/workers/:membershipId/role", async () => {
      calls += 1;
      markRoleStarted();
      await roleGate;
      return HttpResponse.json({ ...WORKER, role_id: HELPER_ROLE.id });
    }),
  );
  renderWithProviders(<TeamPage />);
  await screen.findAllByText(WORKER.email);
  const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
  const row = within(table).getByText(WORKER.email).closest("tr") as HTMLElement;
  await waitFor(() => expect(within(row).getByRole("combobox")).toBeEnabled());
  const helper = within(row).getByRole("option", { name: HELPER_ROLE.name });
  const relief = within(row).getByRole("option", { name: RELIEF_ROLE.name });

  act(() => {
    helper.click();
    relief.click();
  });

  await roleStarted;
  expect(calls).toBe(1);

  await act(async () => {
    releaseRole();
  });
});

import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setCurrentFarmId } from "@/lib/api-client";
import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { ALL_PERMISSIONS, permissionsHandler, server, TEST_FARMS } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import TeamPage from "../page";

const { replace, success } = vi.hoisted(() => ({ replace: vi.fn(), success: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace, prefetch: vi.fn() }), usePathname: () => "/team", useSearchParams: () => new URLSearchParams(), useParams: () => ({}) }));
vi.mock("sonner", () => ({ toast: { success, error: vi.fn() } }));

const successor = { id: 5, user_id: 50, email: "next@example.com", name: "Successor", role_id: 1, role_name: "Keeper", is_active: true, can_reset_password: true, reset_password_block_reason: null, pin_set: false };
const memberships = [successor, { ...successor, id: 6, user_id: 60, email: "pin@example.com", name: "PIN worker", pin_set: true }, { ...successor, id: 7, user_id: 70, email: "inactive@example.com", name: "Inactive worker", is_active: false }];
const payload = { memberships, roles: [], permission_groups: [], permission_labels: {} };

beforeEach(() => {
  replace.mockClear(); success.mockClear();
  Object.assign(HTMLElement.prototype, { hasPointerCapture: () => false, setPointerCapture: () => {}, releasePointerCapture: () => {}, scrollIntoView: () => {} });
  server.use(http.get("/api/team", () => HttpResponse.json(payload)), http.get("/api/auth/permissions", () => HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS })));
});

afterEach(() => {
  localStorage.removeItem(LANGUAGE_STORAGE_KEY);
  document.documentElement.lang = "en";
});

async function prepare() {
  const user = userEvent.setup();
  renderWithProviders(<TeamPage />);
  await screen.findAllByText(successor.email);
  await user.click(screen.getByRole("button", { name: "Transfer ownership" }));
  const dialog = await screen.findByRole("dialog", { name: "Transfer farm ownership" });
  await user.click(within(dialog).getByRole("combobox", { name: "New farm owner" }));
  expect(screen.queryByRole("option", { name: /PIN worker/ })).toBeNull();
  expect(screen.queryByRole("option", { name: /Inactive worker/ })).toBeNull();
  await user.click(await screen.findByRole("option", { name: /Successor/ }));
  return { user, dialog };
}

async function confirm(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
  expect(within(dialog).getByRole("button", { name: "Transfer ownership" })).toBeDisabled();
  await user.type(within(dialog).getByLabelText("Your current password"), "owner-current-secret");
  expect(within(dialog).getByRole("button", { name: "Transfer ownership" })).toBeDisabled();
  await user.click(within(dialog).getByRole("checkbox"));
}

describe("Owner transfer journey", () => {
  it.each(["en", "te"] as const)("shows the %s recipient prompt, supports real keyboard selection, and resets the prompt on reopen", async (language) => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
    const user = userEvent.setup();
    renderWithProviders(<LanguageProvider><TeamPage /></LanguageProvider>);
    await screen.findAllByText(successor.email);
    const action = translate(language, "team.transfer.action");
    await user.click(screen.getByRole("button", { name: action }));
    const dialog = screen.getByRole("dialog", { name: translate(language, "team.transfer.title") });
    const trigger = within(dialog).getByRole("combobox", { name: translate(language, "team.transfer.recipient") });
    const placeholder = translate(language, "team.transfer.recipientPlaceholder");
    expect(trigger).toHaveTextContent(placeholder);
    expect(within(trigger).getByText(placeholder)).toBeVisible();
    expect(trigger).toHaveAttribute("data-placeholder");
    expect(within(dialog).getByRole("button", { name: action })).toBeDisabled();

    // Exercise the installed Base UI trigger/listbox rather than an adapter
    // or hidden native input. Only eligible recipients enter this popup.
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    await screen.findByRole("option", { name: "Successor — next@example.com" });
    expect(screen.queryByRole("option", { name: /PIN worker/ })).toBeNull();
    expect(screen.queryByRole("option", { name: /Inactive worker/ })).toBeNull();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(trigger).toHaveTextContent("Successor — next@example.com"));
    expect(trigger).not.toHaveAttribute("data-placeholder");
    expect(within(trigger).queryByText(placeholder)).toBeNull();
    expect(trigger).toHaveFocus();
    expect(within(dialog).getByRole("button", { name: action })).toBeDisabled();

    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: action }));
    const reopened = screen.getByRole("dialog", { name: translate(language, "team.transfer.title") });
    const resetTrigger = within(reopened).getByRole("combobox", { name: translate(language, "team.transfer.recipient") });
    expect(within(resetTrigger).getByText(placeholder)).toBeVisible();
    expect(resetTrigger).toHaveAttribute("data-placeholder");
    expect(within(reopened).getByRole("button", { name: action })).toBeDisabled();
  });

  it("does not expose transfer authority to a delegated manager", async () => {
    server.use(permissionsHandler(["team.manage"]));
    renderWithProviders(<TeamPage />);
    await screen.findAllByText(successor.email);
    expect(screen.queryByRole("button", { name: "Transfer ownership" })).toBeNull();
  });

  it("requires confirmation and password, freezes a pending transfer, and localizes conflicts", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    let calls = 0;
    server.use(http.post("/api/auth/farms/:farmId/transfer-ownership", async () => { calls += 1; await gate; return HttpResponse.json({ detail: "The new owner must sign in and rotate their password first.", code: "LIFECYCLE_CONFLICT" }, { status: 409 }); }));
    const { user, dialog } = await prepare();
    await confirm(user, dialog);
    await user.dblClick(within(dialog).getByRole("button", { name: "Transfer ownership" }));
    await waitFor(() => expect(calls).toBe(1));
    expect(within(dialog).getByLabelText("Your current password")).toBeDisabled();
    expect(within(dialog).getByRole("combobox")).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(dialog).toBeInTheDocument();
    await act(async () => { release(); });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("This action no longer matches the current state");
    expect(replace).not.toHaveBeenCalled();
  });

  it("refreshes ownership and routes the former owner to farm selection", async () => {
    let body: unknown; let transferred = false; let farmGets = 0;
    server.use(http.get("/api/auth/farms", () => { farmGets += 1; return HttpResponse.json(transferred ? [] : TEST_FARMS); }),
      http.post("/api/auth/farms/:farmId/transfer-ownership", async ({ request, params }) => { body = await request.json(); expect(params.farmId).toBe("1"); transferred = true; return HttpResponse.json(TEST_FARMS[0]); }));
    const { user, dialog } = await prepare();
    await confirm(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Transfer ownership" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/farm-select"));
    expect(body).toEqual({ membership_id: 5, current_password: "owner-current-secret" });
    expect(farmGets).toBeGreaterThan(1);
    expect(success).toHaveBeenCalledWith("Farm ownership transferred. Choose an accessible farm.");
  });

  it("does not refresh or navigate another farm after a stale completion", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    let started = false;
    server.use(http.post("/api/auth/farms/:farmId/transfer-ownership", async () => { started = true; await gate; return HttpResponse.json(TEST_FARMS[0]); }));
    const { user, dialog } = await prepare();
    await confirm(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Transfer ownership" }));
    await waitFor(() => expect(started).toBe(true));
    setCurrentFarmId("2");
    await act(async () => { release(); });
    await waitFor(() => expect(within(dialog).getByLabelText("Your current password")).toBeEnabled());
    expect(replace).not.toHaveBeenCalled(); expect(success).not.toHaveBeenCalled();
  });
});

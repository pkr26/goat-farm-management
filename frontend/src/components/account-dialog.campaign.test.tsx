/**
 * AccountDialog — fresh-domain mutation campaign kills (2026-09): the avatar
 * initials must trim whitespace, the password submit button must announce
 * the in-flight state, and closing the dialog must invalidate a late export
 * failure for the NEXT open (dialog epoch).
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api-client";

import { AccountDialog } from "./account-dialog";
import { settle } from "@/test/settle";

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  authSessionEpochValue: vi.fn(),
  mutateAsync: vi.fn(),
  refreshSessionDetailed: vi.fn(),
  signOut: vi.fn(),
  updateUser: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("@/api/generated/endpoints", () => ({
  useChangePasswordApiAuthChangePasswordPost: () => ({
    mutateAsync: mocks.mutateAsync,
  }),
}));

vi.mock("@/lib/api-client", () => {
  class MockApiError extends Error {
    status: number;
    detail: string;

    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  }
  return {
    ApiError: MockApiError,
    apiFetch: mocks.apiFetch,
    authSessionEpochValue: mocks.authSessionEpochValue,
    refreshSessionDetailed: mocks.refreshSessionDetailed,
  };
});

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ signOut: mocks.signOut, updateUser: mocks.updateUser }),
}));

vi.mock("@/lib/format", () => ({ farmToday: () => "2026-09-09" }));
vi.mock("sonner", () => ({ toast: { success: mocks.toastSuccess } }));

async function openAccount(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^Account/ }));
  return await screen.findByRole("dialog", { name: "Account & password" });
}

describe("AccountDialog — campaign kills", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authSessionEpochValue.mockReturnValue(7);
    mocks.refreshSessionDetailed.mockResolvedValue({
      kind: "session",
      body: { access_token: "rotated", user: { id: 1, email: "e@x.y", name: null } },
    });
  });

  it("trims surrounding whitespace before deriving the avatar initials", () => {
    render(<AccountDialog name={"  Pavan  "} email="pavan@goatfarm.in" />);

    // Without the trim the leading space would leak into the initials.
    expect(screen.getByText("PA")).toBeInTheDocument();
  });

  it("announces the in-flight password change on the submit button", async () => {
    mocks.mutateAsync.mockReturnValue(new Promise(() => undefined));
    const user = userEvent.setup();
    render(<AccountDialog name={null} email="e@goatfarm.in" />);
    const dialog = await openAccount(user);

    expect(
      within(dialog).getByRole("button", { name: "Change password" }),
    ).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Current password for password change"), "old-password-1");
    await user.type(within(dialog).getByLabelText("New password"), "new-password-123");
    await user.type(within(dialog).getByLabelText("Confirm new password"), "new-password-123");
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));

    expect(await within(dialog).findByRole("button", { name: "Changing…" })).toBeDisabled();
  });

  it("surfaces an export failure while the dialog stays open", async () => {
    mocks.apiFetch.mockRejectedValueOnce(new ApiError(503, "export failed"));
    const user = userEvent.setup();
    render(<AccountDialog name={null} email="e@goatfarm.in" />);
    const dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));
    expect(await within(dialog).findByText("export failed")).toBeInTheDocument();
  });

  it("does not restore a late export failure after the dialog was closed and reopened", async () => {
    let rejectExport!: (error: unknown) => void;
    mocks.apiFetch.mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectExport = reject;
      }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name={null} email="e@goatfarm.in" />);
    let dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));
    expect(await within(dialog).findByText("Preparing…")).toBeInTheDocument();

    // Close while the export is still on the wire, then reopen: the close
    // advanced the dialog epoch, so the late failure belongs to a dead view.
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await openAccount(user);

    await act(async () => {
      rejectExport(new ApiError(503, "late export failure"));
    });
    await act(async () => {
      await settle(20);
    });
    expect(within(dialog).queryByText("late export failure")).not.toBeInTheDocument();
    // The action lock must also have been released by the late failure.
    expect(within(dialog).getByRole("button", { name: "Download my data" })).toBeEnabled();
  });
});

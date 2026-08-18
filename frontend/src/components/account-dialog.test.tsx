import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api-client";

import { AccountDialog } from "./account-dialog";

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  mutateAsync: vi.fn(),
  refreshSessionDetailed: vi.fn(),
  signOut: vi.fn(),
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
    refreshSessionDetailed: mocks.refreshSessionDetailed,
  };
});

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ signOut: mocks.signOut }),
}));

vi.mock("@/lib/format", () => ({ farmToday: () => "2026-08-17" }));
vi.mock("sonner", () => ({ toast: { success: mocks.toastSuccess } }));

async function openAccount(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Account" }));
  return screen.getByRole("dialog", { name: "Account & password" });
}

async function fillValidPasswordChange(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
) {
  await user.type(
    within(dialog).getByLabelText("Current password for password change"),
    "old-password",
  );
  await user.type(within(dialog).getByLabelText("New password"), "new-password-123");
  await user.type(
    within(dialog).getByLabelText("Confirm new password"),
    "new-password-123",
  );
}

describe("AccountDialog", () => {
  beforeEach(() => {
    mocks.apiFetch.mockReset();
    mocks.mutateAsync.mockReset();
    mocks.refreshSessionDetailed.mockReset();
    mocks.signOut.mockReset();
    mocks.toastSuccess.mockReset();
    mocks.mutateAsync.mockResolvedValue({ status: 200 });
    mocks.refreshSessionDetailed.mockResolvedValue({
      kind: "session",
      body: {
        access_token: "rotated",
        user: { id: 1, email: "owner@example.test", name: "Owner" },
      },
    });
    mocks.signOut.mockResolvedValue(undefined);
  });

  it("enforces every password field and the twelve-character boundary", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(await within(dialog).findByText("Current password is required")).toBeInTheDocument();
    expect(within(dialog).getByText("Password must be at least 12 characters")).toBeInTheDocument();
    expect(within(dialog).getByText("Confirm the new password")).toBeInTheDocument();
    expect(mocks.mutateAsync).not.toHaveBeenCalled();

    await user.type(
      within(dialog).getByLabelText("Current password for password change"),
      "old-password",
    );
    await user.type(within(dialog).getByLabelText("New password"), "12345678901");
    await user.type(within(dialog).getByLabelText("Confirm new password"), "12345678901");
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(await within(dialog).findByText("Password must be at least 12 characters")).toBeInTheDocument();
    expect(mocks.mutateAsync).not.toHaveBeenCalled();
  });

  it("changes the password, refreshes this session, and closes cleanly", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await fillValidPasswordChange(user, dialog);

    await user.click(within(dialog).getByRole("button", { name: "Change password" }));

    await waitFor(() =>
      expect(mocks.mutateAsync).toHaveBeenCalledWith({
        data: { current_password: "old-password", new_password: "new-password-123" },
      }),
    );
    expect(mocks.refreshSessionDetailed).toHaveBeenCalledOnce();
    expect(mocks.signOut).not.toHaveBeenCalled();
    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      "Password changed. Other signed-in sessions were revoked.",
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("signs out when the server rejects the post-change refresh", async () => {
    mocks.refreshSessionDetailed.mockResolvedValue({ kind: "rejected" });
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await fillValidPasswordChange(user, dialog);

    await user.click(within(dialog).getByRole("button", { name: "Change password" }));

    await waitFor(() => expect(mocks.signOut).toHaveBeenCalledOnce());
    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      "Password changed. Sign in again to continue.",
    );
    expect(mocks.toastSuccess).not.toHaveBeenCalledWith(
      "Password changed. Other signed-in sessions were revoked.",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  // A refresh that never reached the server says nothing about the session.
  // Signing out here would revoke a refresh cookie the backend still honours,
  // on one transient 5xx — and that teardown cannot be undone.
  it.each(["reports unavailable", "throws while refreshing"])(
    "keeps the session when the post-change refresh %s",
    async (failure) => {
      if (failure === "reports unavailable") {
        mocks.refreshSessionDetailed.mockResolvedValue({ kind: "unavailable" });
      } else {
        mocks.refreshSessionDetailed.mockRejectedValue(new Error("refresh failed"));
      }
      const user = userEvent.setup();
      render(<AccountDialog name="Owner" email="owner@example.test" />);
      const dialog = await openAccount(user);
      await fillValidPasswordChange(user, dialog);

      await user.click(within(dialog).getByRole("button", { name: "Change password" }));

      await waitFor(() =>
        expect(mocks.toastSuccess).toHaveBeenCalledWith(
          "Password changed. Reconnecting to your session…",
        ),
      );
      expect(mocks.signOut).not.toHaveBeenCalled();
      expect(mocks.toastSuccess).not.toHaveBeenCalledWith(
        "Password changed. Sign in again to continue.",
      );
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    },
  );

  it("shows an API password error without refreshing the session", async () => {
    mocks.mutateAsync.mockRejectedValue(new ApiError(400, "The current password is incorrect."));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await fillValidPasswordChange(user, dialog);

    await user.click(within(dialog).getByRole("button", { name: "Change password" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The current password is incorrect.",
    );
    expect(mocks.refreshSessionDetailed).not.toHaveBeenCalled();
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

  it("uses a safe fallback for an unexpected password-change failure", async () => {
    mocks.mutateAsync.mockRejectedValue(new Error("network disconnected"));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await fillValidPasswordChange(user, dialog);

    await user.click(within(dialog).getByRole("button", { name: "Change password" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not change the password.",
    );
    expect(mocks.refreshSessionDetailed).not.toHaveBeenCalled();
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

  it("does not restore a late password error after the dialog was closed", async () => {
    let rejectChange: ((reason: unknown) => void) | undefined;
    mocks.mutateAsync.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectChange = reject;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    let dialog = await openAccount(user);
    await fillValidPasswordChange(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledOnce());

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await act(async () => rejectChange?.(new ApiError(400, "Late password failure")));

    dialog = await openAccount(user);
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Current password for password change")).toHaveValue("");
  });

  it("does not let a late password success close a newly reopened dialog", async () => {
    let resolveChange: ((value: { status: number }) => void) | undefined;
    mocks.mutateAsync.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveChange = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    let dialog = await openAccount(user);
    await fillValidPasswordChange(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledOnce());
    expect(
      within(dialog).getByLabelText("Current password for password change"),
    ).toBeDisabled();

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    dialog = await openAccount(user);
    await act(async () => resolveChange?.({ status: 200 }));

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Change password" })).toBeEnabled(),
    );
    expect(screen.getByRole("dialog", { name: "Account & password" })).toBeInTheDocument();
  });

  it("always removes the temporary export link and revokes its object URL", async () => {
    mocks.apiFetch.mockResolvedValue({ account: { id: 1 } });
    const createObjectURL = vi.fn(() => "blob:account-export");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {
      throw new Error("browser blocked the download");
    });
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not download your account data.",
    );
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:account-export");
    expect(document.querySelector('a[download="goatfarm-account-export-2026-08-17.json"]')).toBeNull();
    expect(mocks.toastSuccess).not.toHaveBeenCalled();
    click.mockRestore();
  });

  it("downloads the export with the dated filename and reports success", async () => {
    mocks.apiFetch.mockResolvedValue({ account: { id: 1 } });
    const createObjectURL = vi.fn(() => "blob:account-export");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));

    await waitFor(() =>
      expect(mocks.toastSuccess).toHaveBeenCalledWith(
        "Your account data export was downloaded.",
      ),
    );
    expect(mocks.apiFetch).toHaveBeenCalledWith("/api/auth/account/export");
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(click).toHaveBeenCalledOnce();
    const clickedAnchor = click.mock.instances[0] as HTMLAnchorElement;
    expect(clickedAnchor.download).toBe("goatfarm-account-export-2026-08-17.json");
    expect(clickedAnchor.href).toBe("blob:account-export");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:account-export");
    expect(document.body).not.toContainElement(clickedAnchor);
    click.mockRestore();
  });

  it("shows the API detail when an export request fails", async () => {
    mocks.apiFetch.mockRejectedValue(new ApiError(503, "Export service unavailable."));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Export service unavailable.",
    );
    expect(mocks.toastSuccess).not.toHaveBeenCalled();
  });

  it("does not restore a late export error after the dialog was closed", async () => {
    let rejectExport: ((reason: unknown) => void) | undefined;
    mocks.apiFetch.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectExport = reject;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    let dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await act(async () => rejectExport?.(new ApiError(503, "Export service unavailable")));
    dialog = await openAccount(user);
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Download my data" })).toBeEnabled();
  });

  it("runs only one account action while an export is pending", async () => {
    let rejectExport: ((reason: unknown) => void) | undefined;
    mocks.apiFetch.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectExport = reject;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    await user.type(
      within(dialog).getByLabelText("Current password to delete account"),
      "owner-password",
    );

    const download = within(dialog).getByRole("button", { name: "Download my data" });
    fireEvent.click(download);
    fireEvent.click(download);

    expect(mocks.apiFetch).toHaveBeenCalledOnce();
    expect(mocks.apiFetch).toHaveBeenCalledWith("/api/auth/account/export");
    expect(within(dialog).getByRole("button", { name: "Change password" })).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Delete account and access" }),
    ).toBeDisabled();

    await act(async () => rejectExport?.(new ApiError(503, "Export unavailable")));
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Download my data" })).toBeEnabled(),
    );
  });

  it("clears deletion confirmation state and errors when cancelled", async () => {
    mocks.apiFetch.mockRejectedValue(new ApiError(409, "Owned farms block account deletion."));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    const confirm = within(dialog).getByRole("button", { name: "Delete account and access" });
    expect(confirm).toBeDisabled();
    await user.type(
      within(dialog).getByLabelText("Current password to delete account"),
      "owner-password",
    );
    await user.click(confirm);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Owned farms block account deletion.",
    );

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(within(dialog).queryByText("Owned farms block account deletion.")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    expect(within(dialog).getByLabelText("Current password to delete account")).toHaveValue("");
    expect(within(dialog).getByRole("button", { name: "Delete account and access" })).toBeDisabled();
  });

  it("deletes the account with the confirmation password and signs out", async () => {
    mocks.apiFetch.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    await user.type(
      within(dialog).getByLabelText("Current password to delete account"),
      "owner-password",
    );

    await user.click(within(dialog).getByRole("button", { name: "Delete account and access" }));

    await waitFor(() => expect(mocks.signOut).toHaveBeenCalledOnce());
    expect(mocks.apiFetch).toHaveBeenCalledWith("/api/auth/account", {
      method: "DELETE",
      body: JSON.stringify({ current_password: "owner-password" }),
    });
    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      "Your sign-in identity and profile were removed, and your farm access was disabled. Inactive membership audit anchors and de-identified operational references may remain.",
    );
  });

  it("uses a safe fallback for an unexpected deletion failure", async () => {
    mocks.apiFetch.mockRejectedValue(new Error("network disconnected"));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    await user.type(
      within(dialog).getByLabelText("Current password to delete account"),
      "owner-password",
    );

    await user.click(within(dialog).getByRole("button", { name: "Delete account and access" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not delete your account.",
    );
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

  it("does not restore a late deletion error after the dialog was closed", async () => {
    let rejectDeletion: ((reason: unknown) => void) | undefined;
    mocks.apiFetch.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectDeletion = reject;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    let dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    await user.type(
      within(dialog).getByLabelText("Current password to delete account"),
      "owner-password",
    );
    await user.click(within(dialog).getByRole("button", { name: "Delete account and access" }));
    await waitFor(() => expect(mocks.apiFetch).toHaveBeenCalledOnce());

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await act(async () => rejectDeletion?.(new ApiError(409, "Late deletion failure")));

    dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Current password to delete account")).toHaveValue("");
  });

  it("uses the email as the identity label when no name is available", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name={null} email="owner@example.test" />);
    const dialog = await openAccount(user);

    expect(within(dialog).getByText("owner@example.test")).toBeInTheDocument();
    expect(within(dialog).getAllByText("owner@example.test")).toHaveLength(1);
  });
});

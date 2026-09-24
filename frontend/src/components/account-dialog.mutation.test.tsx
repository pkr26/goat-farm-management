/**
 * Mutation-hardening for src/components/account-dialog.tsx.
 *
 * Pins the trigger's identity contract (the aria-label composed from the
 * display name or email, and the two-letter initials badge built from the
 * trimmed uppercased identity) and the synchronous busy label the password
 * submit button must adopt the instant the action lock is taken — before
 * react-hook-form has flipped isSubmitting on its own.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountDialog } from "./account-dialog";

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

vi.mock("@/lib/format", () => ({ farmToday: () => "2026-08-17" }));
vi.mock("sonner", () => ({ toast: { success: mocks.toastSuccess } }));

async function openAccount(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^Account/ }));
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

describe("AccountDialog trigger identity", () => {
  beforeEach(() => {
    mocks.apiFetch.mockReset();
    mocks.authSessionEpochValue.mockReset().mockReturnValue(1);
    mocks.mutateAsync.mockReset().mockResolvedValue({ status: 200 });
    mocks.refreshSessionDetailed.mockReset().mockResolvedValue({
      kind: "session",
      body: {
        access_token: "rotated",
        user: { id: 1, email: "owner@example.test", name: "Owner" },
      },
    });
    mocks.signOut.mockReset().mockResolvedValue(undefined);
    mocks.updateUser.mockReset();
    mocks.toastSuccess.mockReset();
  });

  it("composes the trigger label from the display name and shows its initials", () => {
    render(<AccountDialog name="Ada Lovelace" email="ada@example.test" />);

    const trigger = screen.getByRole("button", { name: "Account — Ada Lovelace" });
    const initials = trigger.querySelector("span");
    expect(initials).toHaveTextContent("AD");
    expect(trigger).toHaveTextContent("Ada Lovelace");
  });

  it("trims the identity before deriving the two-letter initials", () => {
    render(<AccountDialog name="  Grace  Hopper  " email="grace@example.test" />);

    const initials = screen
      .getByRole("button", { name: /^Account/ })
      .querySelector("span");
    expect(initials).toHaveTextContent("GR");
  });

  it("falls back to the email for the label and its initials", () => {
    render(<AccountDialog name={null} email="owner@example.test" />);

    const trigger = screen.getByRole("button", {
      name: "Account — owner@example.test",
    });
    expect(trigger.querySelector("span")).toHaveTextContent("OW");
    expect(trigger).toHaveTextContent("owner@example.test");
  });

  it("starts every password field empty rather than undefined", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    expect(
      within(dialog).getByLabelText("Current password for password change"),
    ).toHaveValue("");
    expect(within(dialog).getByLabelText("New password")).toHaveValue("");
    expect(within(dialog).getByLabelText("Confirm new password")).toHaveValue("");

    // Empty submission must surface the schema's own copy, which proves the
    // form's values are strings (""), not undefined defaults.
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(
      await within(dialog).findByText("Current password is required"),
    ).toBeInTheDocument();
  });
});

describe("AccountDialog action labels", () => {
  beforeEach(() => {
    mocks.apiFetch.mockReset();
    mocks.authSessionEpochValue.mockReset().mockReturnValue(1);
    mocks.mutateAsync.mockReset().mockResolvedValue({ status: 200 });
    mocks.refreshSessionDetailed.mockReset().mockResolvedValue({
      kind: "session",
      body: {
        access_token: "rotated",
        user: { id: 1, email: "owner@example.test", name: "Owner" },
      },
    });
    mocks.signOut.mockReset().mockResolvedValue(undefined);
    mocks.updateUser.mockReset();
    mocks.toastSuccess.mockReset();
  });

  it("flips the submit button to its busy label as soon as the lock is taken", async () => {
    let resolveMutation: ((value: { status: number }) => void) | undefined;
    mocks.mutateAsync.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveMutation = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await fillValidPasswordChange(user, dialog);

    // fireEvent (not await user.click) so the assertion runs in the window
    // after the synchronous action lock but before react-hook-form marks the
    // form as submitting on its own.
    fireEvent.click(within(dialog).getByRole("button", { name: "Change password" }));

    const busy = within(dialog).getByRole("button", { name: "Changing…" });
    expect(busy).toBeDisabled();
    expect(within(dialog).getByLabelText("New password")).toBeDisabled();

    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledOnce());
    await act(async () => resolveMutation?.({ status: 200 }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("labels the export button as busy while it prepares", async () => {
    let resolveExport: ((value: unknown) => void) | undefined;
    mocks.apiFetch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveExport = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));

    expect(mocks.apiFetch).toHaveBeenCalledWith("/api/auth/account/export");
    expect(within(dialog).getByRole("button", { name: "Preparing…" })).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Change password" }),
    ).toBeDisabled();

    await act(async () => resolveExport?.({ account: { id: 1 } }));
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Download my data" }),
      ).toBeEnabled(),
    );
  });

  it("runs only one export no matter how fast the clicks land", async () => {
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

    const download = within(dialog).getByRole("button", { name: "Download my data" });
    fireEvent.click(download);
    fireEvent.click(download);
    fireEvent.click(download);

    expect(mocks.apiFetch).toHaveBeenCalledTimes(1);
    await act(async () => rejectExport?.(new Error("offline")));
  });
});

/** Mutation-hardening round 2 (2026-09-23 deep-mutation campaign): pins the
 *  password-schema numeric bounds (zod min/max no test exercised at their
 *  boundaries) and the DOM maxLength caps on every password field. The
 *  fireEvent.change route bypasses maxLength so the schema's own .max(128)
 *  is the only guard under test. */
describe("AccountDialog password schema boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authSessionEpochValue.mockReturnValue(1);
    mocks.mutateAsync.mockResolvedValue({ status: 200 });
  });

  async function submitWith(
    user: ReturnType<typeof userEvent.setup>,
    values: { current: string; next: string; confirm: string },
  ) {
    // A validation failure keeps the dialog open; reuse it instead of
    // looking for the trigger button again.
    const existing = screen.queryByRole("dialog", { name: "Account & password" });
    const dialog = existing ?? (await openAccount(user));
    fireEvent.change(within(dialog).getByLabelText("Current password for password change"), {
      target: { value: values.current },
    });
    fireEvent.change(within(dialog).getByLabelText("New password"), {
      target: { value: values.next },
    });
    fireEvent.change(within(dialog).getByLabelText("Confirm new password"), {
      target: { value: values.confirm },
    });
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    return dialog;
  }

  it("accepts a single-character current password (min(1) boundary)", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    await submitWith(user, { current: "x", next: "long-enough-12", confirm: "long-enough-12" });
    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledOnce());
  });

  it("accepts a single-character confirm password (min(1) boundary)", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    // confirm !== new triggers the refine, so pin the min boundary via the
    // standalone field error instead of the submit path.
    const dialog = await openAccount(user);
    await user.type(within(dialog).getByLabelText("New password"), "long-enough-12");
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(await within(dialog).findByText("Confirm the new password")).toBeTruthy();
  });

  it("rejects 129-character current/new/confirm values and accepts exactly 128", async () => {
    const c129 = "c".repeat(129);
    const c128 = "c".repeat(128);
    const next = "n".repeat(128);

    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);

    let dialog = await submitWith(user, { current: c129, next, confirm: next });
    expect(await within(dialog).findByText(/Too big: expected string to have <=128/)).toBeTruthy();
    expect(mocks.mutateAsync).not.toHaveBeenCalled();

    vi.clearAllMocks();
    dialog = await submitWith(user, { current: c128, next: c129, confirm: next });
    expect(await within(dialog).findByText(/Too big: expected string to have <=128/)).toBeTruthy();
    expect(mocks.mutateAsync).not.toHaveBeenCalled();

    vi.clearAllMocks();
    dialog = await submitWith(user, { current: c128, next, confirm: c129 });
    expect(await within(dialog).findByText(/Too big: expected string to have <=128/)).toBeTruthy();
    expect(mocks.mutateAsync).not.toHaveBeenCalled();

    // Exactly 128 everywhere is legal: the mutation goes through.
    vi.clearAllMocks();
    mocks.mutateAsync.mockResolvedValue({ status: 200 });
    await submitWith(user, { current: c128, next, confirm: next });
    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledOnce());
  });

  it("caps typing at 128 characters in every password-change input (maxLength)", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    const fields = [
      "Current password for password change",
      "New password",
      "Confirm new password",
    ] as const;
    for (const label of fields) {
      const input = within(dialog).getByLabelText(label) as HTMLInputElement;
      await user.clear(input);
      await user.type(input, "x".repeat(129));
      expect(input.value.length, label).toBe(128);
    }
  });
});

/** Round 2 tail: a one-character confirm must trip the MISMATCH rule (not
 *  the min-length rule), the delete-account password input caps at 128, and
 *  no account-action error alert exists before any failure. */
describe("AccountDialog boundary tail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authSessionEpochValue.mockReturnValue(1);
    mocks.mutateAsync.mockResolvedValue({ status: 200 });
  });

  it("a one-character confirm password fails as a mismatch, not as too-short", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await user.type(within(dialog).getByLabelText("New password"), "long-enough-12");
    await user.type(within(dialog).getByLabelText("Confirm new password"), "x");
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(await within(dialog).findByText("Passwords do not match")).toBeTruthy();
    expect(within(dialog).queryByText("Confirm the new password")).toBeNull();
  });

  it("caps the delete-account password at 128 characters (maxLength)", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: /delete my account/i }));
    const input = within(dialog).getByLabelText(
      "Current password to delete account",
    ) as HTMLInputElement;
    await user.type(input, "x".repeat(129));
    expect(input.value.length).toBe(128);
  });
});

/**
 * Account dialog TOTP error-field wiring: every password/code input in the four TOTP
 * modes must carry aria-invalid="true" and an aria-describedby pointing at its
 * mode's role="alert" error paragraph while an error is set — and neither attribute
 * once the error clears. Also pins the recovery-code reveal's copied flag: it starts
 * false on the first reveal and resets on dialog close, so a fresh reveal never
 * announces itself as already copied.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountDialog } from "./account-dialog";

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  authSessionEpochValue: vi.fn(),
  mutateAsync: vi.fn(),
  refreshSessionDetailed: vi.fn(),
  signOut: vi.fn(),
  toastSuccess: vi.fn(),
  updateUser: vi.fn(),
  user: null as { id: number; email: string; name: string | null; totp_state: string | null } | null,
}));

vi.mock("@/api/generated/endpoints", () => ({
  useChangePasswordApiAuthChangePasswordPost: () => ({ mutateAsync: mocks.mutateAsync }),
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
  useAuth: () => ({ signOut: mocks.signOut, updateUser: mocks.updateUser, user: mocks.user }),
}));

vi.mock("@/lib/format", () => ({ farmToday: () => "2026-08-17" }));
vi.mock("sonner", () => ({ toast: { success: mocks.toastSuccess } }));

async function openAccount(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^Account/ }));
  return screen.getByRole("dialog", { name: "Account & password" });
}

/** The TOTP section, scoped so the change-password form's identically
 *  labelled "Current password" field cannot match. */
async function totpSection(user: ReturnType<typeof userEvent.setup>) {
  await openAccount(user);
  return screen.getByRole("region", { name: /two-factor authentication/i });
}

function inputById(id: string): HTMLElement {
  return document.getElementById(id) as HTMLElement;
}

/** Both error-wiring attributes on one input, in the with-error state. */
function expectWiredToError(id: string, errorId: string) {
  const el = inputById(id);
  expect(el.getAttribute("aria-invalid")).toBe("true");
  expect(el.getAttribute("aria-describedby")).toBe(errorId);
  expect(document.getElementById(errorId)).not.toBeNull();
}

/** Neither attribute may be present while the input is mounted error-free. */
function expectNotWired(id: string, errorId: string) {
  const el = inputById(id);
  expect(el.getAttribute("aria-invalid")).toBeNull();
  expect(el.getAttribute("aria-describedby")).toBeNull();
  expect(document.getElementById(errorId)).toBeNull();
}

/** Cancel exits the mode (inputs unmount): the alert must leave with it. */
function expectErrorGone(errorId: string) {
  expect(document.getElementById(errorId)).toBeNull();
}

const ENROLLMENT = {
  secret: "JBSWY3DPEHPK3PXP",
  otpauth_uri: "otpauth://totp/Herdly:owner@goatfarm.test?secret=JBSWY3DPEHPK3PXP",
};
const CODES = ["AAAAA-BBBBB", "CCCCC-DDDDD", "EEEEE-FFFFF", "GGGGG-HHHHH", "IIIII-JJJJJ"];

describe("AccountDialog TOTP error-field wiring", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authSessionEpochValue.mockReturnValue(1);
    mocks.user = {
      id: 1,
      email: "owner@goatfarm.test",
      name: "Owner",
      totp_state: null,
    };
  });

  it("enable mode: enrollment failure wires the password input; cancel unwires it", async () => {
    mocks.apiFetch.mockRejectedValueOnce(
      Object.assign(new Error("nope"), { status: 400, detail: "Wrong password." }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    const totp = await totpSection(user);

    await user.click(within(totp).getByRole("button", { name: /enable two-factor…/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    expectNotWired("totp-enable-password", "totp-enable-error");
    await user.click(within(totp).getByRole("button", { name: /start enrollment/i }));

    await waitFor(() =>
      expect(document.getElementById("totp-enable-error")).not.toBeNull(),
    );
    expectWiredToError("totp-enable-password", "totp-enable-error");

    await user.click(within(totp).getByRole("button", { name: /^cancel$/i }));
    expectErrorGone("totp-enable-error");
  });

  it("confirm mode: activation failure wires the code input; cancel unwires it", async () => {
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/auth/totp/enroll") return ENROLLMENT;
      if (path === "/api/auth/totp/confirm")
        throw Object.assign(new Error("nope"), { status: 400, detail: "Bad code." });
      throw new Error(`unexpected ${path}`);
    });
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    const totp = await totpSection(user);

    await user.click(within(totp).getByRole("button", { name: /enable two-factor…/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.click(within(totp).getByRole("button", { name: /start enrollment/i }));
    await user.type(await within(totp).findByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^activate$/i }));

    await waitFor(() =>
      expect(document.getElementById("totp-confirm-error")).not.toBeNull(),
    );
    expectWiredToError("totp-confirm-code", "totp-confirm-error");

    await user.click(within(totp).getByRole("button", { name: /^cancel$/i }));
    expectErrorGone("totp-confirm-error");
  });

  it("disable mode: failure wires BOTH inputs; cancel unwires them", async () => {
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    mocks.apiFetch.mockRejectedValueOnce(
      Object.assign(new Error("nope"), { status: 400, detail: "Wrong password." }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    const totp = await totpSection(user);

    await user.click(within(totp).getByRole("button", { name: /disable two-factor…/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^disable two-factor$/i }));

    await waitFor(() =>
      expect(document.getElementById("totp-disable-error")).not.toBeNull(),
    );
    expectWiredToError("totp-disable-password", "totp-disable-error");
    expectWiredToError("totp-disable-code", "totp-disable-error");

    await user.click(within(totp).getByRole("button", { name: /^cancel$/i }));
    expectErrorGone("totp-disable-error");
  });

  it("regen mode: failure wires BOTH inputs; cancel unwires them", async () => {
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    mocks.apiFetch.mockRejectedValueOnce(
      Object.assign(new Error("nope"), { status: 400, detail: "Wrong password." }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    const totp = await totpSection(user);

    await user.click(within(totp).getByRole("button", { name: /regenerate recovery codes…/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^regenerate codes$/i }));

    await waitFor(() =>
      expect(document.getElementById("totp-regen-error")).not.toBeNull(),
    );
    expectWiredToError("totp-regen-password", "totp-regen-error");
    expectWiredToError("totp-regen-code", "totp-regen-error");

    await user.click(within(totp).getByRole("button", { name: /^cancel$/i }));
    expectErrorGone("totp-regen-error");
  });

  it("recovery reveal starts uncopied and a post-close re-reveal starts uncopied again", async () => {
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/auth/totp/enroll") return ENROLLMENT;
      if (path === "/api/auth/totp/confirm") return { codes: CODES };
      if (path === "/api/auth/totp/recovery/regenerate")
        return { codes: CODES.map((c) => `NEW-${c}`) };
      throw new Error(`unexpected ${path}`);
    });
    const user = userEvent.setup();
    const clipboard = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: clipboard },
      configurable: true,
    });
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    const totp = await totpSection(user);

    // Reveal #1 via activation.
    await user.click(within(totp).getByRole("button", { name: /enable two-factor…/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.click(within(totp).getByRole("button", { name: /start enrollment/i }));
    await user.type(await within(totp).findByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^activate$/i }));
    await within(totp).findByText("AAAAA-BBBBB");
    // A fresh reveal must not announce itself as already copied.
    expect(
      screen.getByRole("button", { name: /^copy codes$/i }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^copy codes$/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^recovery codes copied$/i })).toBeInTheDocument(),
    );

    // Close, reopen, reveal #2 via regeneration: back to uncopied.
    await user.click(screen.getByRole("button", { name: /^close$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    const totp2 = await totpSection(user);
    await user.click(within(totp2).getByRole("button", { name: /regenerate recovery codes…/i }));
    await user.type(within(totp2).getByLabelText(/current password/i), "owner-password-1");
    await user.type(within(totp2).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp2).getByRole("button", { name: /^regenerate codes$/i }));
    await within(totp2).findByText("NEW-AAAAA-BBBBB");
    expect(
      screen.getByRole("button", { name: /^copy codes$/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^recovery codes copied$/i }),
    ).not.toBeInTheDocument();
  });
});

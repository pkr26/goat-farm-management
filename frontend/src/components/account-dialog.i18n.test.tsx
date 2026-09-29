/**
 * Account dialog i18n smoke: with the language set to Telugu the dialog
 * chrome and the zod validation messages resolve through the account.*
 * catalog keys (the password schema is rebuilt from buildPasswordSchema(t)
 * when the language changes), and the field ↔ error association survives
 * the translation.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";

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

async function openAccountInTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  render(
    <LanguageProvider>
      <AccountDialog name="Owner" email="owner@example.test" />
    </LanguageProvider>,
  );
  const trigger = await screen.findByRole("button", { name: /^ఖాతా/ });
  await user.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: "ఖాతా & పాస్‌వర్డ్" });
  return { user, dialog };
}

describe("AccountDialog — Telugu", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.lang = "en";
    vi.clearAllMocks();
    mocks.authSessionEpochValue.mockReturnValue(1);
  });

  it("renders the section headings and password labels in Telugu", async () => {
    const { dialog } = await openAccountInTelugu();

    expect(within(dialog).getByText("మీ డేటా")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("పాస్‌వర్డ్ మార్పు కోసం ప్రస్తుత పాస్‌వర్డ్")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("కొత్త పాస్‌వర్డ్")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("కొత్త పాస్‌వర్డ్ నిర్ధారించండి")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "నా డేటా డౌన్‌లోడ్ చేసుకోండి" })).toBeInTheDocument();
    // No stray English survives on the dialog's key chrome.
    expect(within(dialog).queryByText("Account & password")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Change password")).not.toBeInTheDocument();
  });

  it("shows zod validation messages in Telugu and keeps the error association", async () => {
    const { user, dialog } = await openAccountInTelugu();

    await user.click(within(dialog).getByRole("button", { name: "పాస్‌వర్డ్ మార్చండి" }));

    const currentError = await within(dialog).findByText("ప్రస్తుత పాస్‌వర్డ్ అవసరం", {
      selector: "p[role='alert']",
    });
    expect(currentError).toHaveAttribute("id", "account-current-password-error");
    expect(within(dialog).getByText("పాస్‌వర్డ్ కనీసం 12 అక్షరాలు ఉండాలి")).toBeInTheDocument();
    expect(within(dialog).getByText("కొత్త పాస్‌వర్డ్‌ను నిర్ధారించండి")).toBeInTheDocument();
    expect(
      within(dialog).getByLabelText("పాస్‌వర్డ్ మార్పు కోసం ప్రస్తుత పాస్‌వర్డ్"),
    ).toHaveAccessibleDescription("ప్రస్తుత పాస్‌వర్డ్ అవసరం");
    expect(mocks.mutateAsync).not.toHaveBeenCalled();
  });

  it("keeps the Telugu catalog at parity for the dialog keys", () => {
    expect(translate("te", "account.title")).toBe("ఖాతా & పాస్‌వర్డ్");
    expect(translate("te", "account.validation.mismatch")).toBe("పాస్‌వర్డ్‌లు సరిపోలడం లేదు");
    expect(translate("te", "account.delete.confirm")).toBe("ఖాతా మరియు యాక్సెస్ తొలగించండి");
    expect(translate("te", "totp.recoveryCopyFailed")).toBe(
      "కోడ్‌లు కాపీ చేయలేకపోయాం — వాటిని ఎంచుకుని చేత్తో కాపీ చేసుకోండి.",
    );
  });
});

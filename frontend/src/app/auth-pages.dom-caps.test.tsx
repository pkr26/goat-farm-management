/**
 * Auth-surface DOM caps (2026-10-01 campaign follow-up): login (email 254,
 * password 128, TOTP code 11), register (name 120, email 254, password 128)
 * and the farm-select create form (name 120, location 120, timezone 64) all
 * bound their inputs in the DOM at the schema limits. The TOTP field only
 * renders while a challenge is outstanding, so the challenge response is
 * driven explicitly.
 */

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server, TEST_FARMS } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FarmSelectPage from "./farm-select/page";
import LoginPage from "./login/page";
import RegisterPage from "./register/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

describe("auth page DOM caps", () => {
  beforeEach(() => {
    server.use(
      http.post("/api/auth/login", () => HttpResponse.json({ mfa_token: "challenge-token" })),
      http.get("/api/auth/farms", () => HttpResponse.json(TEST_FARMS)),
    );
  });

  it("login caps email and password, and the TOTP code at 11", async () => {
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    expect(await screen.findByLabelText(/email/i)).toHaveAttribute("maxlength", "254");
    expect(screen.getByLabelText(/password/i)).toHaveAttribute("maxlength", "128");

    await user.type(screen.getByLabelText(/email/i), "owner@goatfarm.test");
    await user.type(screen.getByLabelText(/password/i), "secret-password-1");
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));

    const totp = await screen.findByLabelText(/authenticator code/i);
    expect(totp).toHaveAttribute("maxlength", "11");
  });

  it("register caps name, email and password", async () => {
    renderWithProviders(<RegisterPage />);

    expect(await screen.findByLabelText(/name/i)).toHaveAttribute("maxlength", "120");
    expect(screen.getByLabelText(/email/i)).toHaveAttribute("maxlength", "254");
    expect(screen.getByLabelText(/password/i)).toHaveAttribute("maxlength", "128");
  });

  it("farm-select caps the create-farm form (name 120, location 120, timezone 64)", async () => {
    renderWithProviders(<FarmSelectPage />);

    expect(await screen.findByLabelText(/farm name/i)).toHaveAttribute("maxlength", "120");
    expect(screen.getByLabelText(/location/i)).toHaveAttribute("maxlength", "120");
    expect(screen.getByLabelText(/timezone/i)).toHaveAttribute("maxlength", "64");
  });
});

/**
 * (app) server layout boundary: reads the sidebar's persisted collapse
 * cookie and maps it onto the client shell's defaultOpen, so the first paint
 * matches the operator's last visit (H-2). The client shell itself is
 * covered by layout.test.tsx; the server wrapper sat at 0% (2026-09-28
 * audit, T1) because that file renders AppLayoutClient directly. The shell
 * is stubbed here so the pin is exactly the cookie → defaultOpen mapping.
 */

import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AppLayout from "./layout";

const { sidebarCookie } = vi.hoisted(() => ({
  sidebarCookie: { value: undefined as string | undefined },
}));

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) =>
      name === "sidebar_state" && sidebarCookie.value !== undefined
        ? { value: sidebarCookie.value }
        : undefined,
  }),
}));

vi.mock("./app-layout-client", () => ({
  AppLayoutClient: ({
    defaultOpen,
    children,
  }: {
    defaultOpen: boolean;
    children: ReactNode;
  }) => (
    <div data-testid="app-shell" data-default-open={String(defaultOpen)}>
      {children}
    </div>
  ),
}));

describe("(app) server layout", () => {
  beforeEach(() => {
    sidebarCookie.value = undefined;
  });

  it("opens the sidebar when no collapse state was ever persisted", async () => {
    // Async server component: resolve it to its element tree before render
    // (same pattern as src/app/layout.test.tsx).
    render(await AppLayout({ children: <p>page body</p> }));

    expect(screen.getByTestId("app-shell")).toHaveAttribute("data-default-open", "true");
    expect(screen.getByText("page body")).toBeInTheDocument();
  });

  it("restores the collapsed sidebar the operator left behind", async () => {
    sidebarCookie.value = "false";
    render(await AppLayout({ children: null }));

    expect(screen.getByTestId("app-shell")).toHaveAttribute("data-default-open", "false");
  });
});

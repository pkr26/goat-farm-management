/**
 * Mutation-hardening for src/components/ui/sidebar.tsx.
 *
 * The provider persists its open state in a cookie so a reload keeps the
 * sidebar where the user left it. The cookie must carry SameSite=Lax, a
 * seven-day max-age, and — only when built for production — the Secure
 * attribute that lets it survive an https deployment.
 */

import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider, useSidebar } from "@/components/ui/sidebar";

const viewport = vi.hoisted(() => ({ isMobile: false }));

vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => viewport.isMobile }));

/** jsdom's document.cookie getter never echoes attributes (and a Secure
 * cookie is not even readable over its http origin), so intercept writes. */
let lastCookie: string | undefined;
let cookieDescriptor: PropertyDescriptor | undefined;

beforeEach(() => {
  cookieDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", {
    ...cookieDescriptor,
    configurable: true,
    set(value: string) {
      lastCookie = value;
      cookieDescriptor?.set?.call(document, value);
    },
  });
});

afterEach(() => {
  delete (document as { cookie?: string }).cookie;
  vi.unstubAllEnvs();
  document.cookie = "sidebar_state=; path=/; max-age=0";
});

function renderWithProbe() {
  let setOpen: ((value: boolean) => void) | undefined;
  function Probe() {
    const sidebar = useSidebar();
    setOpen = (value: boolean) => sidebar.setOpen(value);
    return null;
  }
  render(
    <SidebarProvider defaultOpen>
      <Probe />
    </SidebarProvider>,
  );
  return () => setOpen;
}

describe("SidebarProvider cookie persistence", () => {
  it("marks the cookie Secure in a production build", () => {
    vi.stubEnv("NODE_ENV", "production");
    const getSetOpen = renderWithProbe();

    act(() => getSetOpen()?.(false));

    expect(lastCookie).toBeDefined();
    expect(lastCookie).toContain("sidebar_state=false");
    expect(lastCookie).toContain("path=/");
    expect(lastCookie).toContain("max-age=604800");
    expect(lastCookie).toContain("SameSite=Lax");
    expect(lastCookie).toContain("Secure");
  });

  it("omits Secure outside production so the dev server still receives it", () => {
    const getSetOpen = renderWithProbe();

    act(() => getSetOpen()?.(true));

    expect(lastCookie).toBeDefined();
    expect(lastCookie).toContain("sidebar_state=true");
    expect(lastCookie).toContain("SameSite=Lax");
    expect(lastCookie).not.toContain("Secure");
  });
});

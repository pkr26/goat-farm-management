import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import { TEST_USER, server } from "@/test/msw-server";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
}));

async function signOutFromReloadedTab() {
  vi.resetModules();
  const { AuthProvider, useAuth } = await import("@/lib/auth-context");
  let auth: ReturnType<typeof useAuth> | undefined;
  function Probe() { auth = useAuth(); return null; }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><AuthProvider><Probe /></AuthProvider></QueryClientProvider>);
  try {
    await waitFor(() => expect(auth?.user?.email).toBe(TEST_USER.email));
    await act(async () => {
      await expect(Promise.resolve().then(() => auth!.signOut())).resolves.toBeUndefined();
    });
    expect(auth?.user).toBeNull();
  } finally { view.unmount(); client.clear(); }
}

it("keeps reload logout signals fresh on tablets with only getRandomValues", async () => {
  server.use(
    http.get("/api/auth/farms", () => HttpResponse.json([])),
    http.post("/api/auth/logout", () => new HttpResponse(null, { status: 204 })),
  );
  const readStored = Storage.prototype.getItem;
  const clock = vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_000);
  const cryptography = globalThis.crypto;
  vi.stubGlobal("crypto", { getRandomValues: cryptography.getRandomValues.bind(cryptography) });
  vi.stubGlobal("BroadcastChannel", undefined);
  const blockedRead = vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key) {
    if (key === "goatfarm.authEvent") throw new DOMException("Read blocked", "SecurityError");
    return readStored.call(this, key);
  });
  try {
    await signOutFromReloadedTab();
    const previous = readStored.call(localStorage, "goatfarm.authEvent");
    await signOutFromReloadedTab();
    const next = readStored.call(localStorage, "goatfarm.authEvent");
    expect(previous).toMatch(/^logout:/);
    expect(next).toMatch(/^logout:/);
    expect(next).not.toBe(previous);
  } finally { blockedRead.mockRestore(); clock.mockRestore(); vi.unstubAllGlobals(); vi.resetModules(); }
});

it("still tears down the session and writes the fallback marker if entropy is unavailable", async () => {
  server.use(
    http.get("/api/auth/farms", () => HttpResponse.json([])),
    http.post("/api/auth/logout", () => new HttpResponse(null, { status: 204 })),
  );
  vi.stubGlobal("crypto", undefined);
  vi.stubGlobal("BroadcastChannel", undefined);
  try {
    await signOutFromReloadedTab();
    expect(localStorage.getItem("goatfarm.authEvent")).toMatch(/^logout:/);
  } finally { vi.unstubAllGlobals(); vi.resetModules(); }
});

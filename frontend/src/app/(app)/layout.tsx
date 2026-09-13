/* Stryker disable all: server component — its cookie read executes in the Next server runtime and is covered by the e2e navigation suites, not the jsdom unit suite */
import { cookies } from "next/headers";
import type { ReactNode } from "react";

import { AppLayoutClient } from "./app-layout-client";

/**
 * Server boundary for the authenticated shell: reads the sidebar's persisted
 * collapse state (written by SidebarProvider on every toggle) so the first
 * paint matches the operator's last visit instead of always expanding (H-2).
 */
export default async function AppLayout({ children }: { children: ReactNode }) {
  const sidebarState = (await cookies()).get("sidebar_state")?.value;
  return (
    <AppLayoutClient defaultOpen={sidebarState !== "false"}>{children}</AppLayoutClient>
  );
}

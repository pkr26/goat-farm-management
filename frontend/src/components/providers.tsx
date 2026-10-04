"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useState, type ReactNode } from "react";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

import { AuthProvider } from "@/lib/auth-context";
import { LanguageProvider, type Language } from "@/lib/i18n";
import { DocumentTitleSync } from "@/components/document-title-sync";

export function Providers({
  children,
  nonce,
  initialLanguage,
}: {
  children: ReactNode;
  nonce?: string;
  initialLanguage?: Language | null;
}) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 },
        },
      }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      {/* nonce: next-themes' inline no-flash bootstrap must carry the
       * request's CSP nonce or the policy (src/proxy.ts, M-1 2026-09-20)
       * blocks it. */}
      <ThemeProvider
        attribute="class"
        defaultTheme="system"
        enableSystem
        disableTransitionOnChange
        nonce={nonce}
      >
        <AuthProvider>
          <LanguageProvider initialLanguage={initialLanguage}>
            <DocumentTitleSync />
            <TooltipProvider>
              {children}
              {/* top-center: on phones the top-right corner is the least
               * visible spot (thumb reach + notch), and it stays out of the
               * way on desktop too. richColors stays off: the toast hues
               * come from the app's status tokens (see ui/sonner.tsx), not
               * the library palette (2026-09-28 audit). */}
              <Toaster position="top-center" />
            </TooltipProvider>
          </LanguageProvider>
        </AuthProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

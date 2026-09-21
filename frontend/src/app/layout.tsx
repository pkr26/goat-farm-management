import type { Metadata } from "next";
import { Fraunces, Inter, JetBrains_Mono, Noto_Sans_Telugu } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";
import { Providers } from "@/components/providers";
import { APP_NAME } from "@/lib/brand";

// Nonce-based CSP (src/proxy.ts, M-1 2026-09-20): Next.js stamps the nonce on
// its scripts during server rendering, which only happens for dynamically
// rendered pages — a statically prerendered shell has no request headers to
// read a nonce from. Every route under this layout therefore renders per
// request.
export const dynamic = "force-dynamic";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
});

// Inter and Fraunces have no Telugu glyphs; Noto Sans Telugu covers the
// script and joins the sans/heading stacks for `lang="te"` (globals.css).
const notoSansTelugu = Noto_Sans_Telugu({
  subsets: ["telugu", "latin"],
  variable: "--font-noto-sans-telugu",
});

export const metadata: Metadata = {
  title: {
    default: `${APP_NAME} — Goat farm management`,
    template: `%s · ${APP_NAME}`,
  },
  description:
    "Goat farm management — herd, health, breeding, kidding and finance in one place.",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // The proxy minted this request's CSP nonce; next-themes' inline theme
  // bootstrap must carry it too or the policy blocks it (M-1, 2026-09-20).
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${fraunces.variable} ${jetbrainsMono.variable} ${notoSansTelugu.variable}`}
    >
      <body className="min-h-screen bg-background text-foreground antialiased">
        <Providers nonce={nonce}>{children}</Providers>
      </body>
    </html>
  );
}

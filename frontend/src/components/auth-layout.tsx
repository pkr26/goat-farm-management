"use client";

import { HeartPulse, PawPrint, TrendingUp } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Logo } from "@/components/logo";

/**
 * Shared split-screen shell for /login and /register. The brand panel's
 * gradient derives from the --primary token (no hard-coded emerald), so
 * it follows the theme and any future rebrand. Copy is species-neutral:
 * the product serves goat and buffalo dairy farms.
 */

const BRAND_GRADIENT =
  "linear-gradient(165deg, color-mix(in oklch, var(--primary) 92%, white 8%) 0%, var(--primary) 42%, color-mix(in oklch, var(--primary) 55%, black 10%) 100%)";

const FEATURES: { icon: LucideIcon; title: string; description: string }[] = [
  {
    icon: PawPrint,
    title: "Complete herd records",
    description: "Track every animal, tag and lineage in one place.",
  },
  {
    icon: HeartPulse,
    title: "Proactive health care",
    description: "Stay ahead of vaccinations, treatments and checkups.",
  },
  {
    icon: TrendingUp,
    title: "Insights that pay off",
    description: "Breeding, kidding and finance reports at a glance.",
  },
];

export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <main className="flex min-h-screen">
      {/* Brand panel (desktop) */}
      <div
        className="relative hidden w-[46%] flex-col justify-between overflow-hidden p-12 text-primary-foreground lg:flex"
        style={{ backgroundImage: BRAND_GRADIENT }}
      >
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -top-24 -right-24 size-96 rounded-full bg-white/10 blur-3xl"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-32 -left-16 size-80 rounded-full bg-black/10 blur-3xl"
        />
        <div className="relative">
          <Logo className="[&>span:first-child]:bg-white/15 [&>span:first-child]:ring-1 [&>span:first-child]:ring-white/15" />
        </div>
        <div className="relative space-y-10">
          <div className="space-y-4">
            <h1 className="font-heading text-4xl leading-tight font-semibold tracking-tight">
              Herd management,
              <br />
              simplified.
            </h1>
            <p className="max-w-md text-lg text-primary-foreground/85">
              Run a healthier, more profitable farm — from the first tag to
              the final sale.
            </p>
          </div>
          <ul className="space-y-5">
            {FEATURES.map((feature) => (
              <li key={feature.title} className="flex items-start gap-3.5">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-white/12 ring-1 ring-white/15">
                  <feature.icon className="size-[18px]" aria-hidden="true" />
                </span>
                <span>
                  <span className="block text-sm font-semibold">
                    {feature.title}
                  </span>
                  <span className="block text-sm text-primary-foreground/75">
                    {feature.description}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-sm text-primary-foreground/65">
          For Osmanabadi goat herds and Murrah dairies across Telangana.
        </p>
      </div>

      {/* Form side */}
      <div className="relative flex flex-1 flex-col bg-background">
        {/* Compact brand bar (mobile) */}
        <div
          className="px-4 py-3 text-primary-foreground lg:hidden"
          style={{ backgroundImage: BRAND_GRADIENT }}
        >
          <Logo className="[&>span:first-child]:bg-white/15 [&>span:first-child]:ring-1 [&>span:first-child]:ring-white/15" />
        </div>
        <div className="flex flex-1 items-center justify-center px-4 py-10 sm:px-8">
          <div className="w-full max-w-sm">
            <div className="mb-8 space-y-1.5">
              <h2 className="font-heading text-2xl font-semibold tracking-tight">
                {title}
              </h2>
              <p className="text-sm text-muted-foreground">{subtitle}</p>
            </div>
            {children}
            <p className="mt-6 text-center text-sm text-muted-foreground">
              {footer}
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}

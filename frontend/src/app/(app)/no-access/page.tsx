"use client";

import { ShieldQuestion } from "lucide-react";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";

export default function NoAccessPage() {
  return (
    <div className="relative mx-auto max-w-lg space-y-3 py-16 text-center">
      <span
        aria-hidden="true"
        className="pointer-events-none absolute -top-6 left-1/2 size-40 -translate-x-1/2 rounded-full bg-primary/5 blur-2xl"
      />
      <span className="relative inline-flex size-12 items-center justify-center rounded-2xl bg-muted text-muted-foreground">
        <ShieldQuestion className="size-6" aria-hidden="true" />
      </span>
      <h1 className="relative font-heading text-2xl font-semibold tracking-tight">
        No farm modules assigned
      </h1>
      <p className="relative text-sm text-muted-foreground">
        Your role on this farm does not currently include access to a module. Ask the farm owner
        to update your role, or choose another farm.
      </p>
      <div className="relative pt-1">
        <Link href="/farm-select" className={buttonVariants({ variant: "outline" })}>
          Choose another farm
        </Link>
      </div>
    </div>
  );
}

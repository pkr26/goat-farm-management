"use client";

import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";

export default function NoAccessPage() {
  return (
    <div className="mx-auto max-w-lg space-y-3 py-12 text-center">
      <h1 className="text-xl font-semibold">No farm modules assigned</h1>
      <p className="text-sm text-muted-foreground">
        Your role on this farm does not currently include access to a module. Ask the farm owner
        to update your role, or choose another farm.
      </p>
      <Link href="/farm-select" className={buttonVariants({ variant: "outline" })}>
        Choose another farm
      </Link>
    </div>
  );
}

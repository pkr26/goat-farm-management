"use client";

/** 404 fallback inside the app shell. */

import { Compass } from "lucide-react";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { firstPermittedPath } from "@/lib/permission-navigation";
import { usePermissions } from "@/lib/use-permissions";

export default function NotFound() {
  const permissions = usePermissions();
  const destination =
    permissions.loading || permissions.isError ? "/farm-select" : firstPermittedPath(permissions.can);
  return (
    <div className="space-y-3 py-12 text-center">
      <span className="inline-flex size-10 items-center justify-center rounded-xl bg-muted text-muted-foreground">
        <Compass className="size-5" aria-hidden="true" />
      </span>
      <p className="font-heading text-lg font-semibold">Page not found</p>
      <p className="text-sm text-muted-foreground">
        The page you&apos;re looking for doesn&apos;t exist.
      </p>
      <Link href={destination} className={buttonVariants({ variant: "outline" })}>
        Back to an available page
      </Link>
    </div>
  );
}

"use client";

/**
 * App-shell 404 fallback. Resolve copy from stored language so the page can render
 * without its provider.
 */

import { Compass } from "lucide-react";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { readStoredLanguage, translate } from "@/lib/i18n";
import { firstPermittedPath } from "@/lib/permission-navigation";
import { usePermissions } from "@/lib/use-permissions";

export default function NotFound() {
  const permissions = usePermissions();
  const language = readStoredLanguage();
  const destination =
    permissions.loading || permissions.isError ? "/farm-select" : firstPermittedPath(permissions.can);
  return (
    <div className="space-y-3 py-12 text-center">
      <span className="inline-flex size-10 items-center justify-center rounded-xl bg-muted text-muted-foreground">
        <Compass className="size-5" aria-hidden="true" />
      </span>
      <p className="font-heading text-lg font-semibold">
        {translate(language, "error.notFound.title")}
      </p>
      <p className="text-sm text-muted-foreground">
        {translate(language, "error.notFound.description")}
      </p>
      <Link href={destination} className={buttonVariants({ variant: "outline" })}>
        {translate(language, "error.notFound.back")}
      </Link>
    </div>
  );
}

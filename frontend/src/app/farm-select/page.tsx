"use client";

/** Farm picker (also used for "switch farm") + new-farm creation. */

import { zodResolver } from "@hookform/resolvers/zod";
import { Home, Loader2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import type { PermissionsOut } from "@/api/generated/models";
import { Logo } from "@/components/logo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch, ApiError } from "@/lib/api-client";
import { useAuth, type FarmEntry } from "@/lib/auth-context";
import {
  firstPermittedPathFromList,
  permittedAppPathFromList,
} from "@/lib/permission-navigation";

const farmSchema = z.object({
  name: z.string().min(1, "Name is required").max(120),
  location: z.string().max(120).optional(),
  timezone: z.string().min(1, "Timezone is required").max(64),
});
type FarmValues = z.infer<typeof farmSchema>;

function FarmSelectPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, farms, farmId, loading, selectFarm, refreshFarms } = useAuth();
  const [serverError, setServerError] = useState<string | null>(null);
  const [selectingFarmId, setSelectingFarmId] = useState<number | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FarmValues>({
    resolver: zodResolver(farmSchema),
    defaultValues: { name: "", location: "", timezone: "Asia/Kolkata" },
  });

  async function pick(farm: FarmEntry) {
    setServerError(null);
    setSelectingFarmId(farm.id);
    selectFarm(farm.id);
    try {
      const permissions = await apiFetch<PermissionsOut>("/api/auth/permissions");
      const requestedPath = permittedAppPathFromList(
        searchParams.get("returnTo"),
        permissions.permissions,
      );
      router.push(requestedPath ?? firstPermittedPathFromList(permissions.permissions));
    } catch (error) {
      setServerError(
        error instanceof ApiError ? error.detail : "Could not load permissions for this farm.",
      );
    } finally {
      setSelectingFarmId(null);
    }
  }

  async function onSubmit(values: FarmValues) {
    setServerError(null);
    try {
      const farm = await apiFetch<FarmEntry>("/api/auth/farms", {
        method: "POST",
        body: JSON.stringify({ ...values, location: values.location || null }),
      });
      await refreshFarms();
      reset({ name: "", location: "", timezone: "Asia/Kolkata" });
      await pick(farm);
    } catch (err) {
      setServerError(
        err instanceof ApiError ? err.detail : "Could not create the farm.",
      );
    }
  }

  if (loading || !user) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-muted/40">
        <Loader2 className="size-6 animate-spin text-primary" />
        <p className="text-muted-foreground">Loading…</p>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/40 p-4 sm:p-6">
      <div className="w-full max-w-2xl space-y-6">
        <div className="space-y-3 text-center">
          <Logo className="justify-center" />
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold">Your farms</h1>
            <p className="text-sm text-muted-foreground">
              Choose a farm to continue, or create a new one below.
            </p>
          </div>
        </div>

        {farms.length === 0 && (
          <p className="text-center text-muted-foreground">
            No farms yet — create your first one below.
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          {farms.map((farm) => (
            <button
              key={farm.id}
              type="button"
              disabled={selectingFarmId !== null}
              onClick={() => void pick(farm)}
              className="rounded-xl border bg-card p-4 text-left shadow-sm transition hover:border-primary hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2.5">
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                    <Home className="size-4" />
                  </span>
                  <span className="truncate font-medium">
                    {selectingFarmId === farm.id ? "Opening…" : farm.name}
                  </span>
                </span>
                {farm.id === farmId && <Badge>current</Badge>}
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                {farm.location ?? "—"} · {farm.role ?? "Owner"}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {(farm as FarmEntry & { timezone?: string }).timezone ?? "Asia/Kolkata"}
              </p>
            </button>
          ))}
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Create a farm</CardTitle>
            <CardDescription>Owners can manage multiple farms.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
              <div className="space-y-1.5">
                <Label htmlFor="name">Farm name</Label>
                <Input
                  id="name"
                  aria-invalid={Boolean(errors.name) || undefined}
                  aria-describedby={errors.name ? "farm-name-error" : undefined}
                  {...register("name")}
                />
                {errors.name && (
                  <p id="farm-name-error" role="alert" className="text-sm text-destructive">
                    {errors.name.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="location">Location (optional)</Label>
                <Input id="location" {...register("location")} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="timezone">Farm timezone</Label>
                <Input
                  id="timezone"
                  list="common-timezones"
                  autoComplete="off"
                  aria-invalid={Boolean(errors.timezone) || undefined}
                  aria-describedby="timezone-hint"
                  {...register("timezone")}
                />
                <datalist id="common-timezones">
                  <option value="Asia/Kolkata" />
                  <option value="America/Phoenix" />
                  <option value="America/Los_Angeles" />
                  <option value="America/Chicago" />
                  <option value="America/New_York" />
                  <option value="Europe/London" />
                  <option value="Australia/Sydney" />
                </datalist>
                <p id="timezone-hint" className="text-xs text-muted-foreground">
                  IANA name used for due dates and daily records, for example Asia/Kolkata.
                </p>
                {errors.timezone && (
                  <p role="alert" className="text-sm text-destructive">
                    {errors.timezone.message}
                  </p>
                )}
              </div>
              {serverError && (
                <p role="alert" className="text-sm text-destructive">
                  {serverError}
                </p>
              )}
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Creating…" : "Create farm"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}

export default function FarmSelectPage() {
  return (
    <Suspense fallback={<p className="py-10 text-center text-muted-foreground">Loading…</p>}>
      <FarmSelectPageContent />
    </Suspense>
  );
}

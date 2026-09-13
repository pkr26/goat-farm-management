"use client";

/** Farm picker (also used for "switch farm") + new-farm creation. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, LogOut, PawPrint } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import type { FarmCreateIn } from "@/api/generated/models";
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
import { apiFetch, ApiError, authSessionEpochValue } from "@/lib/api-client";
import { useAuth, type FarmEntry } from "@/lib/auth-context";
import {
  firstPermittedPathFromList,
  permittedAppPathFromList,
} from "@/lib/permission-navigation";
import { fetchSharedPermissions } from "@/lib/permission-envelope";
import { useSingleFlight } from "@/lib/use-single-flight";
import { farmTypeLabel } from "@/lib/farm-vocabulary";


const farmSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Name is required")
    .max(120, "Farm name must be at most 120 characters"),
  location: z
    .string()
    .trim()
    .max(120, "Location must be at most 120 characters")
    .optional(),
  timezone: z
    .string()
    .trim()
    .min(1, "Timezone is required")
    .max(64, "Timezone must be at most 64 characters")
    // Do not use Intl as an IANA validator here: browser tzdata can lag the
    // server and reject a newly introduced, otherwise valid zone. These are
    // the two tzdata placeholders the API explicitly forbids in every version.
    .refine(
      (timezone) => timezone !== "Factory" && timezone !== "localtime",
      "Enter a real location timezone",
    ),
});
type FarmValues = z.infer<typeof farmSchema>;

function FarmSelectPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, farms, farmId, loading, selectFarm, refreshFarms, signOut } = useAuth();
  const queryClient = useQueryClient();
  const [serverError, setServerError] = useState<string | null>(null);
  const [selectingFarmId, setSelectingFarmId] = useState<number | null>(null);
  // Stryker disable next-line BooleanLiteral: the mount effect below overwrites the initial value before any continuation can observe it
  const mounted = useRef(true);
  const farmTransition = useSingleFlight();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FarmValues>({
    resolver: zodResolver(farmSchema),
    defaultValues: { name: "", location: "", timezone: "Asia/Kolkata" },
  });

  // Stryker disable ArrayDeclaration: a constant string dep never changes, so the effect still runs exactly once
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  // Stryker restore ArrayDeclaration

  async function openFarm(farm: FarmEntry) {
    // Stryker disable next-line ConditionalExpression: openFarm runs synchronously inside the click handler, so the provider is necessarily mounted here
    if (!mounted.current) return;
    const sessionEpoch = authSessionEpochValue();
    setServerError(null);
    setSelectingFarmId(farm.id);
    // A just-created farm may not be in AuthProvider's cached list when the
    // best-effort refresh fails, but the creation response already carries
    // the authoritative timezone.
    selectFarm(farm.id, farm.timezone);
    try {
      // Stryker disable StringLiteral: only read in permission-envelope's unreachable non-200 branch; the catch below never surfaces it (it renders its own fallback copy)
      const permissions = await fetchSharedPermissions(
        queryClient,
        "Could not load permissions for this farm.",
      );
      // Stryker restore StringLiteral
      if (!mounted.current || authSessionEpochValue() !== sessionEpoch) return;
      const requestedPath = permittedAppPathFromList(
        searchParams.get("returnTo"),
        permissions.permissions,
      );
      router.push(requestedPath ?? firstPermittedPathFromList(permissions.permissions));
      // Deliberately leave `selectingFarmId` set on the success path. router.push
      // is asynchronous, and useSingleFlight releases its guard as soon as this
      // function returns — i.e. while the navigation is still committing.
      // Re-enabling the list there let a second farm be picked mid-transition,
      // whose selectFarm() swaps the API client's X-Farm-Id, so the operator
      // landed on farm A's route with farm B's header. This page unmounts on a
      // successful navigation, so the flag has no later life.
    } catch (error) {
      if (!mounted.current || authSessionEpochValue() !== sessionEpoch) return;
      setServerError(
        error instanceof ApiError ? error.detail : "Could not load permissions for this farm.",
      );
      // The operator stays here and must be able to retry.
      setSelectingFarmId(null);
    }
  }

  async function pick(farm: FarmEntry) {
    await farmTransition.run(() => openFarm(farm));
  }

  async function onSubmit(values: FarmValues) {
    await farmTransition.run(async () => {
      // Stryker disable next-line ConditionalExpression: onSubmit runs synchronously inside the submit event, so the provider is necessarily mounted here
      if (!mounted.current) return;
      const sessionEpoch = authSessionEpochValue();
      setServerError(null);
      let farm: FarmEntry;
      try {
        const payload = {
          ...values,
          location: values.location || null,
        } satisfies FarmCreateIn;
        farm = await apiFetch<FarmEntry>("/api/auth/farms", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      } catch (err) {
        if (!mounted.current || authSessionEpochValue() !== sessionEpoch) return;
        setServerError(
          err instanceof ApiError ? err.detail : "Could not create the farm.",
        );
        return;
      }
      if (!mounted.current || authSessionEpochValue() !== sessionEpoch) return;
      // The farm is durably created from here on. Never report a follow-up
      // failure as a creation failure: the retry would mint a fresh
      // Idempotency-Key and create a second, identical farm.
      reset({ name: "", location: "", timezone: "Asia/Kolkata" });
      try {
        await refreshFarms();
      } catch {
        // Best-effort: the list is re-fetched on the next load, and openFarm()
        // below takes the operator straight into the farm they just created.
      }
      if (!mounted.current || authSessionEpochValue() !== sessionEpoch) return;
      await openFarm(farm);
    });
  }

  if (loading || !user) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-muted/40">
        <Loader2 className="size-6 animate-spin text-primary" />
        <p role="status" aria-live="polite" className="text-muted-foreground">Loading…</p>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/40 p-4 sm:p-6">
      <div className="w-full max-w-2xl space-y-6">
        <div className="relative space-y-3 text-center">
          {/* This screen sits outside the app shell, which owns the only other
              sign-out control — a just-registered user must still be able to
              leave (e.g. they registered the wrong account). */}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="absolute right-0 top-0"
            onClick={() => void signOut()}
            disabled={loading}
          >
            <LogOut aria-hidden="true" />
            Sign out
          </Button>
          <Logo className="justify-center" />
          <div className="space-y-1">
            <h1 className="font-heading text-2xl font-semibold tracking-tight">Your farms</h1>
            <p className="text-sm text-muted-foreground">
              Choose a farm to continue, or create a new one below.
            </p>
          </div>
        </div>

        {farms.length === 0 && (
          <p className="text-center text-sm text-muted-foreground">
            No farms yet — create your first one below.
          </p>
        )}
        {farms.length > 0 && (
          <div className="grid gap-3 sm:grid-cols-2">
            {farms.map((farm) => {
              return (
                <button
                  key={farm.id}
                  type="button"
                  disabled={farmTransition.pending || selectingFarmId !== null}
                  onClick={() => void pick(farm)}
                  className="group/farm-card relative rounded-xl bg-card p-4 text-left shadow-xs ring-1 ring-foreground/[0.07] transition hover:-translate-y-0.5 hover:shadow-md hover:ring-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-success-tint text-success-tint-foreground">
                      <PawPrint className="size-[18px]" aria-hidden="true" />
                    </span>
                    {farm.id === farmId && <Badge variant="success">current</Badge>}
                  </div>
                  <span className="mt-3 block truncate font-medium">
                    {selectingFarmId === farm.id ? "Opening…" : farm.name}
                  </span>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {farm.location ?? "—"} · {farm.role ?? "Owner"}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground/80">
                    <span>
                      {farmTypeLabel}
                    </span>
                    {" · "}
                    <span>{farm.timezone}</span>
                  </p>
                </button>
              );
            })}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Create a farm</CardTitle>
            <CardDescription>Owners can manage multiple farms.</CardDescription>
          </CardHeader>
          <CardContent>
            <form
              onSubmit={(event) => void handleSubmit(onSubmit)(event)}
              noValidate
            >
              <fieldset
                disabled={isSubmitting || farmTransition.pending}
                className="min-w-0 space-y-4"
              >
              <div className="space-y-1.5">
                <Label htmlFor="name">Farm name</Label>
                <Input
                  id="name"
                  maxLength={120}
                  placeholder="e.g. Your farm name"
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
                <Input
                  id="location"
                  maxLength={120}
                  placeholder="Village, district or landmark"
                  aria-invalid={Boolean(errors.location) || undefined}
                  aria-describedby={errors.location ? "farm-location-error" : undefined}
                  {...register("location")}
                />
                {errors.location && (
                  <p
                    id="farm-location-error"
                    role="alert"
                    className="text-sm text-destructive"
                  >
                    {errors.location.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="timezone">Farm timezone</Label>
                <Input
                  id="timezone"
                  list="common-timezones"
                  maxLength={64}
                  autoComplete="off"
                  aria-invalid={Boolean(errors.timezone) || undefined}
                  aria-describedby={
                    errors.timezone ? "timezone-hint timezone-error" : "timezone-hint"
                  }
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
                  <p id="timezone-error" role="alert" className="text-sm text-destructive">
                    {errors.timezone.message}
                  </p>
                )}
              </div>
              {serverError && (
                <p role="alert" className="text-sm text-destructive">
                  {serverError}
                </p>
              )}
              <Button type="submit" className="w-full sm:w-auto" disabled={isSubmitting || farmTransition.pending}>
                {isSubmitting ? "Creating…" : "Create farm"}
              </Button>
              </fieldset>
            </form>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}

export default function FarmSelectPage() {
  return (
    <Suspense fallback={<p role="status" aria-live="polite" className="py-10 text-center text-muted-foreground">Loading…</p>}>
      <FarmSelectPageContent />
    </Suspense>
  );
}

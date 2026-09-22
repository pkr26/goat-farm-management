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
import { InlineLoading } from "@/components/skeletons";
import { LanguageToggle } from "@/components/language-toggle";
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
import { useT, type TFn } from "@/lib/i18n";
import { useAuth, type FarmEntry } from "@/lib/auth-context";
import { mapServerError } from "@/lib/server-error-phrases";
import {
  firstPermittedPathFromList,
  permittedAppPathFromList,
} from "@/lib/permission-navigation";
import { fetchSharedPermissions } from "@/lib/permission-envelope";
import { useSingleFlight } from "@/lib/use-single-flight";
import { farmTypeLabel } from "@/lib/farm-vocabulary";


/** Localized twin of the old module-scope schema (ITEM 5): validation copy
 * reaches Telugu-first operators in their language, not only the chrome. */
function makeFarmSchema(t: TFn) {
  return z.object({
    name: z
      .string()
      .trim()
      .min(1, t("farmSelect.errors.nameRequired"))
      .max(120, t("farmSelect.errors.nameTooLong")),
    location: z
      .string()
      .trim()
      .max(120, t("farmSelect.errors.locationTooLong"))
      .optional(),
    timezone: z
      .string()
      .trim()
      .min(1, t("farmSelect.errors.timezoneRequired"))
      .max(64, t("farmSelect.errors.timezoneTooLong"))
      // Do not use Intl as an IANA validator here: browser tzdata can lag the
      // server and reject a newly introduced, otherwise valid zone. These are
      // the two tzdata placeholders the API explicitly forbids in every version.
      .refine(
        (timezone) => timezone !== "Factory" && timezone !== "localtime",
        t("farmSelect.errors.timezoneInvalid"),
      ),
  });
}
type FarmValues = z.infer<ReturnType<typeof makeFarmSchema>>;

function FarmSelectPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, farms, farmId, loading, selectFarm, refreshFarms, signOut } = useAuth();
  const t = useT();
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
    resolver: zodResolver(makeFarmSchema(t)),
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
        error instanceof ApiError
          ? mapServerError(t, error.detail, error.status, error.code)
          : t("farmSelect.permissionsFailed"),
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
          err instanceof ApiError
            ? mapServerError(t, err.detail, err.status, err.code)
            : t("farmSelect.createFailed"),
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
        <p role="status" aria-live="polite" className="text-muted-foreground">{t("common.loading")}</p>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/40 p-4 sm:p-6">
      <div className="w-full max-w-2xl space-y-6">
        <div className="relative space-y-3 text-center">
          {/* ITEM 5: the picker is the first screen a Telugu-first operator
              customizes — the toggle must exist here, not only in the app shell. */}
          <div className="flex justify-center">
            <LanguageToggle />
          </div>
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
            {t("farmSelect.signOut")}
          </Button>
          <Logo className="justify-center" />
          <div className="space-y-1">
            <h1 className="font-heading text-2xl font-semibold tracking-tight">
              {t("farmSelect.title")}
            </h1>
            <p className="text-sm text-muted-foreground">
              {t("farmSelect.subtitle")}
            </p>
          </div>
        </div>

        {farms.length === 0 && (
          <p className="text-center text-sm text-muted-foreground">
            {t("farmSelect.empty")}
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
                    {farm.id === farmId && <Badge variant="success">{t("farmSelect.current")}</Badge>}
                  </div>
                  <span className="mt-3 block truncate font-medium">
                    {selectingFarmId === farm.id ? t("farmSelect.opening") : farm.name}
                  </span>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {farm.location ?? "—"} · {farm.role ?? t("farmSelect.ownerRole")}
                  </p>
                  {/* Full-strength muted token: the /80 tint sat under 4.5:1
                  (sub-AA microtext, 2026-09-21 audit). */}
              <p className="mt-1 text-xs text-muted-foreground">
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
            <CardTitle>{t("farmSelect.createTitle")}</CardTitle>
            <CardDescription>{t("farmSelect.multiOwner")}</CardDescription>
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
                <Label htmlFor="name">{t("farmSelect.nameLabel")}</Label>
                <Input
                  id="name"
                  maxLength={120}
                  placeholder={t("farmSelect.namePlaceholder")}
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
                <Label htmlFor="location">{t("farmSelect.locationLabel")}</Label>
                <Input
                  id="location"
                  maxLength={120}
                  placeholder={t("farmSelect.locationPlaceholder")}
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
                <Label htmlFor="timezone">{t("farmSelect.timezoneLabel")}</Label>
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
                  {t("farmSelect.timezoneHint")}
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
                {isSubmitting ? t("farmSelect.creating") : t("farmSelect.createButton")}
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
    <Suspense fallback={<InlineLoading className="justify-center py-10" />}>
      <FarmSelectPageContent />
    </Suspense>
  );
}

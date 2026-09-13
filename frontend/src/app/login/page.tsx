"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import type { LoginIn, TokenOut } from "@/api/generated/models";
import { AuthLayout } from "@/components/auth-layout";
import { LanguageToggle } from "@/components/language-toggle";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch, ApiError, authSessionEpochValue } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { useT } from "@/lib/i18n";
import {
  firstPermittedPathFromList,
  permittedAppPathFromList,
} from "@/lib/permission-navigation";
import { fetchSharedPermissions } from "@/lib/permission-envelope";
import { useSingleFlight } from "@/lib/use-single-flight";


const loginSchema = z.object({
  email: z
    .string()
    .trim()
    .email("Enter a valid email address")
    .max(254, "Email must be at most 254 characters"),
  password: z
    .string()
    .min(1, "Password is required")
    .max(128, "Password must be at most 128 characters"),
});
type LoginValues = z.infer<typeof loginSchema>;

function LoginPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { signIn, getFarms } = useAuth();
  const queryClient = useQueryClient();
  const t = useT();
  const [serverError, setServerError] = useState<string | null>(null);
  const [forgotOpen, setForgotOpen] = useState(false);
  // Stryker disable next-line BooleanLiteral: the mount effect below overwrites the initial value before any continuation can observe it
  const mounted = useRef(true);
  const submission = useSingleFlight();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });

  // Stryker disable ArrayDeclaration: a constant string dep never changes, so the effect still runs exactly once
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  // Stryker restore ArrayDeclaration

  async function onSubmit(values: LoginValues) {
    await submission.run(async () => {
      setServerError(null);
      try {
        const payload: LoginIn = values;
        const body = await apiFetch<TokenOut>(
          "/api/auth/login",
          { method: "POST", body: JSON.stringify(payload) },
        );
        if (!mounted.current) return;
        await signIn(body.access_token, body.user);
        if (!mounted.current) return;
        // A farmless account has no permissions context yet: the call would
        // 422 (no X-Farm-Id to validate against). Farm selection is the
        // required next step anyway, so go there without the doomed request.
        const signedInEpoch = authSessionEpochValue();
        if (getFarms().length === 0) {
          // signIn resolved synchronously above: nothing can have changed the
          // mount state or the session epoch between there and here, so no
          // re-check is needed before this navigation.
          router.push("/farm-select");
          return;
        }
        try {
          // Stryker disable StringLiteral: only read in permission-envelope's unreachable non-200 branch (fetchQuery rejects non-2xx first); this page's catch never surfaces it
          const permissions = await fetchSharedPermissions(
            queryClient,
            "Could not load permissions.",
          );
          // Stryker restore StringLiteral
          if (!mounted.current || authSessionEpochValue() !== signedInEpoch) return;
          // A session-expiry deep link keeps its destination: the farm-switch
          // variant of the validator demotes record ids the new farm cannot
          // own, exactly as /farm-select does (L1).
          const requested = permittedAppPathFromList(
            searchParams.get("returnTo"),
            permissions.permissions,
          );
          router.push(requested ?? firstPermittedPathFromList(permissions.permissions));
        } catch {
          // A user with no farm has no permissions context yet; farm selection
          // is also the safe recovery path for a transient discovery failure.
          // A forced logout is different: its epoch change already owns the
          // navigation and this stale login continuation must stay out.
          if (mounted.current && authSessionEpochValue() === signedInEpoch) {
            router.push("/farm-select");
          }
        }
      } catch (err) {
        // Stryker disable next-line ConditionalExpression: the only guarded statement is setServerError, a no-op on an unmounted component
        if (!mounted.current) return;
        // Surface the server's own message for every API error (429 rate
        // limit, 422 password policy, 5xx); only a network failure gets the
        // human connection guidance — never dev-flavoured copy.
        setServerError(
          err instanceof ApiError
            ? err.status === 401
              ? t("login.invalidCredentials")
              : err.detail
            : t("login.networkError"),
        );
      }
    });
  }

  return (
    <AuthLayout
      title="Welcome back"
      subtitle="Sign in to your account"
      footer={
        <>
          No account?{" "}
          <Link href="/register" className="font-medium text-primary underline-offset-4 hover:underline">
            Register
          </Link>
        </>
      }
    >
      {/* Language first: a low-literacy Telugu worker must be able to switch
       * before reading anything else on the card. */}
      <div className="mb-4 flex justify-end">
        <LanguageToggle />
      </div>
      <form onSubmit={(event) => void handleSubmit(onSubmit)(event)} noValidate>
        <fieldset
          disabled={isSubmitting || submission.pending}
          className="min-w-0 space-y-4"
        >
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              maxLength={254}
              aria-invalid={!!errors.email}
              aria-describedby={errors.email ? "email-error" : undefined}
              {...register("email")}
            />
            {errors.email && (
              <p id="email-error" role="alert" className="text-sm text-destructive">
                {errors.email.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              maxLength={128}
              aria-invalid={!!errors.password}
              aria-describedby={errors.password ? "password-error" : undefined}
              {...register("password")}
            />
            {errors.password && (
              <p id="password-error" role="alert" className="text-sm text-destructive">
                {errors.password.message}
              </p>
            )}
          </div>
          {serverError && (
            <p role="alert" className="text-sm text-destructive">
              {serverError}
            </p>
          )}
          <Button
            type="submit"
            className="w-full"
            disabled={isSubmitting || submission.pending}
          >
            {isSubmitting || submission.pending ? "Signing in…" : "Sign in"}
          </Button>
        </fieldset>
      </form>
      {/* Honest recovery path: worker passwords are owner-reset from the
       * Team page; there is no email recovery yet, so the dialog explains
       * instead of promising a reset link. */}
      <div className="mt-4 text-center">
        <button
          type="button"
          className="text-sm font-medium text-primary underline-offset-4 hover:underline"
          onClick={() => setForgotOpen(true)}
        >
          {t("login.forgotPassword")}
        </button>
      </div>
      <Dialog open={forgotOpen} onOpenChange={setForgotOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("login.forgotTitle")}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{t("login.forgotBody")}</p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setForgotOpen(false)}>
              {t("common.close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AuthLayout>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageContent />
    </Suspense>
  );
}

"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import type { LoginIn, LoginOut, TokenOut, UserOut } from "@/api/generated/models";
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
import { useT, type TFn } from "@/lib/i18n";
import {
  firstPermittedPathFromList,
  permittedAppPathFromList,
} from "@/lib/permission-navigation";
import { fetchSharedPermissions } from "@/lib/permission-envelope";
import { useSingleFlight } from "@/lib/use-single-flight";


/** The schema is rebuilt per language so inline validation messages follow
 * the worker's chosen language (the same pattern as the duty form). Exported
 * for direct schema-level testing. */
export function makeLoginSchema(t: TFn) {
  return z.object({
    email: z
      .string()
      .trim()
      .email(t("auth.emailInvalid"))
      .max(254, t("auth.emailTooLong")),
    password: z
      .string()
      .min(1, t("auth.passwordRequired"))
      .max(128, t("auth.passwordTooLong")),
    // Present only while a TOTP challenge is outstanding (2026-09-16):
    // the server issued an mfa_token and expects the 6-digit code.
    totp: z
      .string()
      .trim()
      .regex(/^[0-9]{6}$/, t("login.totpCodeInvalid"))
      .optional(),
  });
}
type LoginValues = z.infer<ReturnType<typeof makeLoginSchema>>;

function LoginPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { signIn, getFarms } = useAuth();
  const queryClient = useQueryClient();
  const t = useT();
  const [serverError, setServerError] = useState<string | null>(null);
  const [forgotOpen, setForgotOpen] = useState(false);
  // Non-null while the password succeeded and a TOTP challenge is pending.
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  // Stryker disable next-line BooleanLiteral: the mount effect below overwrites the initial value before any continuation can observe it
  const mounted = useRef(true);
  const submission = useSingleFlight();
  const loginSchema = useMemo(() => makeLoginSchema(t), [t]);
  const {
    register,
    handleSubmit,
    setValue,
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

  /** Everything after a session exists (sign-in + navigation) — shared by
   * the password path and the TOTP challenge path. */
  async function continueSignedIn(accessToken: string, user: UserOut) {
    await signIn(accessToken, user);
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
  }

  async function onSubmit(values: LoginValues) {
    await submission.run(async () => {
      setServerError(null);
      try {
        if (mfaToken !== null) {
          // Second step: exchange the challenge token + code for a session.
          const body = await apiFetch<TokenOut>("/api/auth/totp/challenge", {
            method: "POST",
            body: JSON.stringify({ mfa_token: mfaToken, code: values.totp ?? "" }),
          });
          if (!mounted.current) return;
          setMfaToken(null);
          await continueSignedIn(body.access_token, body.user);
          return;
        }
        const payload: LoginIn = {
          email: values.email,
          password: values.password,
        };
        // LoginOut is the generated contract: either an mfa_token (second
        // factor demanded, no session material) or a full session. Typing the
        // response through the generated model means a backend rename breaks
        // tsc here instead of failing at runtime (2026-09-17 re-audit).
        const body = await apiFetch<LoginOut>(
          "/api/auth/login",
          { method: "POST", body: JSON.stringify(payload) },
        );
        if (!mounted.current) return;
        if (body.mfa_token) {
          // Password accepted; the account demands a TOTP code. No session
          // material exists yet.
          setMfaToken(body.mfa_token);
          return;
        }
        if (!body.access_token || !body.user) {
          // Contract violation: LoginOut always carries either the challenge
          // or the full session. Fail closed with the generic message.
          setServerError(t("login.networkError"));
          return;
        }
        await continueSignedIn(body.access_token, body.user);
      } catch (err) {
        // Stryker disable next-line ConditionalExpression: the only guarded statement is setServerError, a no-op on an unmounted component
        if (!mounted.current) return;
        // Surface the server's own message for every API error (429 rate
        // limit, 422 password policy, 5xx); only a network failure gets the
        // human connection guidance — never dev-flavoured copy.
        setServerError(
          err instanceof ApiError
            ? err.status === 401
              ? mfaToken !== null
                ? t("login.totpCodeInvalid")
                : t("login.invalidCredentials")
              : err.detail
            : t("login.networkError"),
        );
      }
    });
  }

  return (
    <AuthLayout
      title={t("auth.welcomeBack")}
      subtitle={t("auth.signInSubtitle")}
      footer={
        <>
          {t("auth.noAccount")}{" "}
          <Link href="/register" className="font-medium text-primary underline-offset-4 hover:underline">
            {t("auth.registerLink")}
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
          {/* Credentials only for the password step: during the code step the
           * typed password must not sit on screen while the user fetches
           * their phone (react-hook-form keeps the values, so Back restores
           * them without retyping). */}
          {mfaToken === null && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="email">{t("auth.email")}</Label>
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
                <Label htmlFor="password">{t("auth.password")}</Label>
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
            </>
          )}
          {mfaToken !== null && (
            <div className="space-y-1.5">
              <p className="text-sm text-muted-foreground">{t("login.totpPrompt")}</p>
              <Label htmlFor="totp">{t("login.totpCodeLabel")}</Label>
              <Input
                id="totp"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={6}
                aria-invalid={!!errors.totp}
                aria-describedby={errors.totp ? "totp-error" : undefined}
                {...register("totp")}
              />
              {errors.totp && (
                <p id="totp-error" role="alert" className="text-sm text-destructive">
                  {errors.totp.message}
                </p>
              )}
            </div>
          )}
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
            {isSubmitting || submission.pending
              ? t("auth.signingIn")
              : mfaToken !== null
                ? t("login.verifyCode")
                : t("auth.signIn")}
          </Button>
          {mfaToken !== null && (
            <Button
              type="button"
              variant="outline"
              className="w-full"
              disabled={isSubmitting || submission.pending}
              onClick={() => {
                setMfaToken(null);
                setServerError(null);
                // Drop any partial/invalid code draft with the step:
                // react-hook-form retains it, and on the next password
                // submit zod would reject the stale value silently (the
                // totp error only renders inside this branch) — a sign-in
                // no-op with zero feedback until a page reload.
                setValue("totp", undefined, { shouldValidate: false });
              }}
            >
              {t("login.totpBack")}
            </Button>
          )}
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

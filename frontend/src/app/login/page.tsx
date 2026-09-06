"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  getPermissionsApiAuthPermissionsGetQueryOptions,
} from "@/api/generated/endpoints";
import type { LoginIn, PermissionsOut, TokenOut } from "@/api/generated/models";
import { AuthLayout } from "@/components/auth-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch, ApiError, authSessionEpochValue } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import {
  firstPermittedPathFromList,
  permittedAppPathFromList,
} from "@/lib/permission-navigation";
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
  const [serverError, setServerError] = useState<string | null>(null);
  const mounted = useRef(true);
  const submission = useSingleFlight();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

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
          if (mounted.current && authSessionEpochValue() === signedInEpoch) {
            router.push("/farm-select");
          }
          return;
        }
        try {
          // Fetch through the shared query cache so the shell's
          // usePermissions consumers hit this result instead of refetching.
          const envelope = await queryClient.fetchQuery(
            getPermissionsApiAuthPermissionsGetQueryOptions(),
          );
          if (!mounted.current || authSessionEpochValue() !== signedInEpoch) return;
          // The fetch core rejects non-2xx before an envelope is built, so a
          // settled envelope is always 200; the narrowing is for the type
          // system only, not a reachable error path.
          const permissions =
            envelope.status === 200 ? (envelope.data as PermissionsOut) : null;
          if (!permissions) {
            throw new ApiError(envelope.status, "Could not load permissions.");
          }
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
        if (!mounted.current) return;
        // Surface the server's own message for every API error (429 rate
        // limit, 422 password policy, 5xx) — only a network failure gets the
        // "is the backend running?" fallback.
        setServerError(
          err instanceof ApiError
            ? err.status === 401
              ? "Invalid email or password."
              : err.detail
            : "Could not sign in — is the backend running?",
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

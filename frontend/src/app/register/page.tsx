"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { AuthLayout } from "@/components/auth-layout";
import { LanguageToggle } from "@/components/language-toggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch, ApiError } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { useT, type TFn } from "@/lib/i18n";
import type { RegisterIn, TokenOut } from "@/api/generated/models";
import { useSingleFlight } from "@/lib/use-single-flight";

/** Rebuilt per language so inline validation messages localize. Exported for
 * direct schema-level testing. */
export function makeRegisterSchema(t: TFn) {
  return z.object({
    name: z.string().max(120).optional(),
    email: z
      .string()
      .trim()
      .email(t("auth.emailInvalid"))
      .max(254, t("auth.emailTooLong")),
    password: z
      .string()
      .min(12, t("auth.passwordTooShort"))
      .max(128, t("auth.passwordTooLong")),
  });
}
type RegisterValues = z.infer<ReturnType<typeof makeRegisterSchema>>;

export default function RegisterPage() {
  const router = useRouter();
  const { signIn } = useAuth();
  const t = useT();
  const [serverError, setServerError] = useState<string | null>(null);
  // Stryker disable next-line BooleanLiteral: the mount effect below overwrites the initial value before any continuation can observe it
  const mounted = useRef(true);
  const submission = useSingleFlight();
  const registerSchema = useMemo(() => makeRegisterSchema(t), [t]);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterValues>({ resolver: zodResolver(registerSchema) });

  // Stryker disable ArrayDeclaration: a constant string dep never changes, so the effect still runs exactly once
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  // Stryker restore ArrayDeclaration

  async function onSubmit(values: RegisterValues) {
    await submission.run(async () => {
      setServerError(null);
      try {
        const payload = {
          ...values,
          // Stryker disable next-line OptionalChaining: the name input is registered unconditionally, so the value is a string, never undefined
          name: values.name?.trim() || null,
        } satisfies RegisterIn;
        const body = await apiFetch<TokenOut>(
          "/api/auth/register",
          {
            method: "POST",
            body: JSON.stringify(payload),
          },
        );
        if (!mounted.current) return;
        await signIn(body.access_token, body.user);
        if (mounted.current) router.push("/farm-select");
      } catch (err) {
        // Stryker disable next-line ConditionalExpression: the only guarded statement is setServerError, a no-op on an unmounted component
        if (!mounted.current) return;
        // Surface the server's own message for every API error (400 duplicate
        // email, 429 rate limit, 422 password policy, 5xx); only a network
        // failure gets the human connection guidance — never dev-flavoured
        // copy.
        setServerError(
          err instanceof ApiError ? err.detail : t("register.networkError"),
        );
      }
    });
  }

  return (
    <AuthLayout
      title={t("auth.createTitle")}
      subtitle={t("auth.createSubtitle")}
      footer={
        <>
          {t("auth.haveAccount")}{" "}
          <Link href="/login" className="font-medium text-primary underline-offset-4 hover:underline">
            {t("auth.signIn")}
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
            <Label htmlFor="name">{t("auth.nameOptional")}</Label>
            <Input
              id="name"
              autoComplete="name"
              maxLength={120}
              aria-invalid={Boolean(errors.name) || undefined}
              aria-describedby={errors.name ? "register-name-error" : undefined}
              {...register("name")}
            />
            {errors.name && (
              <p id="register-name-error" role="alert" className="text-sm text-destructive">
                {errors.name.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="email">{t("auth.email")}</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              maxLength={254}
              aria-invalid={Boolean(errors.email) || undefined}
              aria-describedby={errors.email ? "register-email-error" : undefined}
              {...register("email")}
            />
            {errors.email && (
              <p id="register-email-error" role="alert" className="text-sm text-destructive">
                {errors.email.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">{t("auth.password")}</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              maxLength={128}
              aria-invalid={Boolean(errors.password) || undefined}
              aria-describedby={errors.password ? "register-password-error" : undefined}
              {...register("password")}
            />
            {errors.password && (
              <p id="register-password-error" role="alert" className="text-sm text-destructive">
                {errors.password.message}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {t("auth.passwordHint")}
            </p>
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
            {isSubmitting || submission.pending
              ? t("auth.creatingAccount")
              : t("auth.createAccount")}
          </Button>
        </fieldset>
      </form>
    </AuthLayout>
  );
}

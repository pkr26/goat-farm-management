"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { HeartPulse, PawPrint, TrendingUp } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Logo } from "@/components/logo";
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
import { useAuth } from "@/lib/auth-context";
import type { TokenOut } from "@/api/generated/models";

const loginSchema = z.object({
  email: z.string().email("Enter a valid email address"),
  password: z.string().min(1, "Password is required"),
});
type LoginValues = z.infer<typeof loginSchema>;

const FEATURES = [
  {
    icon: PawPrint,
    title: "Complete herd records",
    description: "Track every animal, tag and lineage in one place.",
  },
  {
    icon: HeartPulse,
    title: "Proactive health care",
    description: "Stay ahead of vaccinations, treatments and checkups.",
  },
  {
    icon: TrendingUp,
    title: "Insights that pay off",
    description: "Breeding, kidding and finance reports at a glance.",
  },
];

export default function LoginPage() {
  const router = useRouter();
  const { signIn } = useAuth();
  const [serverError, setServerError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });

  async function onSubmit(values: LoginValues) {
    setServerError(null);
    try {
      const body = await apiFetch<TokenOut>(
        "/api/auth/login",
        { method: "POST", body: JSON.stringify(values) },
      );
      await signIn(body.access_token, body.user);
      router.push("/dashboard");
    } catch (err) {
      // Surface the server's own message for every API error (429 rate
      // limit, 422 password policy, 5xx) — only a network failure gets the
      // "is the backend running?" fallback (audit 6-4/7-2/8-3).
      setServerError(
        err instanceof ApiError
          ? err.status === 401
            ? "Invalid email or password."
            : err.detail
          : "Could not sign in — is the backend running?",
      );
    }
  }

  return (
    <main className="flex min-h-screen">
      {/* Brand panel (desktop) */}
      <div className="hidden w-1/2 flex-col justify-between bg-gradient-to-br from-emerald-700 via-emerald-800 to-emerald-950 p-10 text-emerald-50 lg:flex">
        <Logo />
        <div className="space-y-8">
          <div className="space-y-3">
            <h1 className="font-heading text-3xl font-semibold tracking-tight">
              Herd management, simplified.
            </h1>
            <p className="max-w-md text-emerald-100/80">
              GoatFarm helps you run a healthier, more profitable farm — from
              the first tag to the final sale.
            </p>
          </div>
          <ul className="space-y-5">
            {FEATURES.map((feature) => (
              <li key={feature.title} className="flex items-start gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-white/10">
                  <feature.icon className="size-4" />
                </span>
                <span>
                  <span className="block text-sm font-medium">{feature.title}</span>
                  <span className="block text-sm text-emerald-100/70">
                    {feature.description}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-sm text-emerald-100/60">
          Built for goat farmers, by goat farmers.
        </p>
      </div>

      {/* Form side */}
      <div className="flex flex-1 flex-col">
        {/* Compact brand header (mobile) */}
        <div className="bg-gradient-to-r from-emerald-700 to-emerald-900 px-4 py-3 text-emerald-50 lg:hidden">
          <Logo />
        </div>
        <div className="flex flex-1 items-center justify-center p-4 sm:p-8">
          <Card className="w-full max-w-sm">
            <CardHeader>
              <CardTitle className="text-2xl">Welcome back</CardTitle>
              <CardDescription>Sign in to your account</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
                <div className="space-y-1.5">
                  <Label htmlFor="email">Email</Label>
                  <Input
                    id="email"
                    type="email"
                    autoComplete="email"
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
                <Button type="submit" className="w-full" disabled={isSubmitting}>
                  {isSubmitting ? "Signing in…" : "Sign in"}
                </Button>
              </form>
              <p className="mt-4 text-center text-sm text-muted-foreground">
                No account?{" "}
                <Link href="/register" className="text-primary underline">
                  Register
                </Link>
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </main>
  );
}

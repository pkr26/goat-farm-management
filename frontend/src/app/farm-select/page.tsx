"use client";

/** Farm picker (also used for "switch farm") + new-farm creation. */

import { zodResolver } from "@hookform/resolvers/zod";
import { Home, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

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

const farmSchema = z.object({
  name: z.string().min(1, "Name is required").max(120),
  location: z.string().max(120).optional(),
});
type FarmValues = z.infer<typeof farmSchema>;

export default function FarmSelectPage() {
  const router = useRouter();
  const { user, farms, farmId, loading, selectFarm, refreshFarms } = useAuth();
  const [serverError, setServerError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FarmValues>({ resolver: zodResolver(farmSchema) });

  function pick(farm: FarmEntry) {
    selectFarm(farm.id);
    router.push("/dashboard");
  }

  async function onSubmit(values: FarmValues) {
    setServerError(null);
    try {
      const farm = await apiFetch<FarmEntry>("/api/auth/farms", {
        method: "POST",
        body: JSON.stringify({ ...values, location: values.location || null }),
      });
      await refreshFarms();
      reset();
      pick(farm);
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
              onClick={() => pick(farm)}
              className="rounded-xl border bg-card p-4 text-left shadow-sm transition hover:border-primary hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2.5">
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                    <Home className="size-4" />
                  </span>
                  <span className="truncate font-medium">{farm.name}</span>
                </span>
                {farm.id === farmId && <Badge>current</Badge>}
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                {farm.location ?? "—"} · {farm.role ?? "Owner"}
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
                <Input id="name" {...register("name")} />
                {errors.name && (
                  <p className="text-sm text-destructive">{errors.name.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="location">Location (optional)</Label>
                <Input id="location" {...register("location")} />
              </div>
              {serverError && <p className="text-sm text-destructive">{serverError}</p>}
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

"use client";

/** Farm picker (also used for "switch farm") + new-farm creation. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

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
  location: z.string().max(200).optional(),
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
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-muted-foreground">Loading…</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-2xl space-y-6 p-6">
      <h1 className="text-2xl font-semibold">Your farms</h1>
      {farms.length === 0 && (
        <p className="text-muted-foreground">
          No farms yet — create your first one below.
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {farms.map((farm) => (
          <button
            key={farm.id}
            onClick={() => pick(farm)}
            className="rounded-lg border p-4 text-left transition hover:border-primary"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium">{farm.name}</span>
              {farm.id === farmId && <Badge>current</Badge>}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
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
    </main>
  );
}

"use client";

import { useState } from "react";
import type { SalesAssumptions } from "@/api/generated/models";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/lib/i18n";

export function parseFestivalDates(raw: string): string[] | null | false {
  if (!raw.trim()) return null;
  const dates = raw.split(",").map((value) => value.trim());
  if (dates.length > 40 || new Set(dates).size !== dates.length) return false;
  for (const date of dates) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return false;
    const parsed = new Date(`${date}T00:00:00Z`);
    if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) return false;
  }
  return dates.sort();
}

export function FestivalDateEditor({ value, onChange, onValidityChange }: {
  value: SalesAssumptions;
  onChange: (patch: Partial<SalesAssumptions>) => void;
  onValidityChange: (valid: boolean) => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState<string | null>(null);
  const dates = draft ?? (value.festival_date_overrides ?? []).join(", ");
  const invalid = parseFestivalDates(dates) === false;
  return <fieldset className="space-y-2 rounded-lg border p-3 sm:col-span-2 lg:col-span-3">
    <Label htmlFor="sim-festival-dates">{t("simulation.festival.title")}</Label>
    <p id="sim-festival-help" className="text-xs text-muted-foreground">{t("simulation.festival.help")}</p>
    <Input id="sim-festival-dates" value={dates} aria-invalid={invalid || undefined}
      aria-describedby={`sim-festival-help${invalid ? " sim-festival-error" : ""}`}
      onChange={(event) => {
        setDraft(event.target.value);
        const parsed = parseFestivalDates(event.target.value);
        onValidityChange(parsed !== false);
        if (parsed !== false) onChange({ festival_date_overrides: parsed });
      }} />
    {invalid && <p id="sim-festival-error" role="alert" className="text-sm text-destructive">
      {t("simulation.festival.invalid")}
    </p>}
    <Label htmlFor="sim-festival-source">{t("simulation.festival.source")}</Label>
    <Input id="sim-festival-source" maxLength={500} value={value.festival_date_source ?? ""}
      onChange={(event) => onChange({ festival_date_source: event.target.value || null })} />
  </fieldset>;
}

"use client";

/** Presentational building blocks of the assumptions editor: the headline
 *  metric card with its explain affordance, and the per-field "?" help
 *  button. */

import { Info, type LucideIcon } from "lucide-react";

import { StatCard } from "@/components/stat-card";

/** Headline metric: shared StatCard with tabular numerals, plus an optional
 * "explain" button in the hint slot that opens the metric's dialog. */
export function MetricCard({
  value,
  label,
  icon,
  tint = "default",
  onInfo,
}: {
  value: string;
  label: string;
  icon: LucideIcon;
  tint?: "default" | "success" | "warning" | "destructive";
  onInfo?: () => void;
}) {
  return (
    <StatCard
      label={label}
      value={<span className="tabular-nums">{value}</span>}
      icon={icon}
      tint={tint}
      hint={
        onInfo ? (
          <button
            type="button"
            aria-label={`Explain ${label}`}
            onClick={onInfo}
            className="inline-flex size-4 items-center justify-center rounded-full border border-muted-foreground/40 text-muted-foreground hover:bg-accent"
          >
            <Info className="size-2.5" aria-hidden />
          </button>
        ) : undefined
      }
    />
  );
}


/** Contents of the field-explanation dialog opened by a "?" button. */
export type FieldHelpState = {
  label: string;
  body: string | null;
  facts: { term: string; value: string }[];
};

/** The "?" affordance beside every assumption label: opens the field's
 *  plain-language explanation — what the term is and what the values mean.
 *  Swallows the click so it never toggles a surrounding <summary>/<details>. */
export function FieldHelpButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={`What is ${label}?`}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onClick();
      }}
      className="inline-flex size-4 shrink-0 items-center justify-center rounded-full border border-muted-foreground/40 text-[10px] font-semibold leading-none text-muted-foreground hover:bg-accent"
    >
      {/* The accessible name is content-based (not an aria-label): an
          aria-label containing the field name collides with getByLabelText
          queries for the input the adjacent <Label> points at. */}
      <span className="sr-only">Explain {label}</span>?
    </button>
  );
}

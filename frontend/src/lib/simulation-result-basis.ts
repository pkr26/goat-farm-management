import type { SimulationAssumptions, ViabilityMetrics } from "@/api/generated/models";
import type { MessageKey, TFn } from "@/lib/i18n";

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => [key, canonical(item)]));
  }
  return value;
}

/** Compare complete execution snapshots independent of object property order. */
export function assumptionsIdentity(assumptions: SimulationAssumptions): string {
  return JSON.stringify(canonical({ ...assumptions, events: assumptions.events ?? [] }));
}

/** Old results remain readable; explicit ambiguous/unproven values are withheld. */
export function assessedIrr(metrics: ViabilityMetrics): number | null {
  return metrics.irr_status && metrics.irr_status !== "unique" ? null : metrics.irr;
}

const IRR_LABELS: Record<string, MessageKey> = {
  unique: "simulation.irr.unique", multiple_roots: "simulation.irr.multiple_roots",
  no_root: "simulation.irr.no_root", indeterminate: "simulation.irr.indeterminate",
  not_assessed: "simulation.irr.not_assessed",
};
const NLM_LABELS: Record<string, MessageKey> = {
  ineligible: "simulation.nlm.ineligible", unsupported_unit: "simulation.nlm.unsupported_unit",
  eligible_cost_unknown: "simulation.nlm.eligible_cost_unknown", estimate_only: "simulation.nlm.estimate_only",
  approved_unscheduled: "simulation.nlm.approved_unscheduled", approved_scheduled: "simulation.nlm.approved_scheduled",
  not_assessed: "simulation.nlm.not_assessed",
};
export function irrStatusLabel(status: string | undefined, t: TFn): string {
  return t(IRR_LABELS[status ?? "not_assessed"] ?? "simulation.irr.not_assessed");
}
export function nlmStatusLabel(status: string | undefined, t: TFn): string {
  return t(NLM_LABELS[status ?? "not_assessed"] ?? "simulation.nlm.not_assessed");
}

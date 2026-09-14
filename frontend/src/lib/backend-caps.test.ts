import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import * as caps from "./backend-caps";

/**
 * Cross-layer parity: the frontend's mirrored caps must equal the bounds the
 * generated API contract carries (shared/openapi.json is CI-enforced against
 * the backend). A backend cap change that forgets this module fails here.
 */

function schemaBound(
  spec: Record<string, unknown>,
  schemaName: string,
  property: string,
  bound: "maxLength" | "maximum",
): number | null {
  const components = (spec as { components?: { schemas?: Record<string, unknown> } }).components;
  const schemas = components?.schemas;
  const schema = schemas?.[schemaName] as Record<string, unknown> | undefined;
  if (!schema) return null;
  const props = schema.properties as Record<string, Record<string, unknown>> | undefined;
  const prop = props?.[property];
  if (!prop) return null;
  if (typeof prop[bound] === "number") return prop[bound] as number;
  // Optional fields wrap the bound inside anyOf variants.
  const anyOf = prop.anyOf as Array<Record<string, unknown>> | undefined;
  for (const variant of anyOf ?? []) {
    if (typeof variant[bound] === "number") return variant[bound] as number;
  }
  return null;
}

describe("frontend caps mirror the generated API contract", () => {
  const specPath = path.resolve(__dirname, "../../../shared/openapi.json");
  const spec = JSON.parse(readFileSync(specPath, "utf8")) as Record<string, unknown>;

  it("animal tag length matches AnimalCreateIn", () => {
    // The bound lives inside anyOf for the optional field.
    expect(schemaBound(spec, "AnimalCreateIn", "tag_number", "maxLength")).toBe(
      caps.MAX_ANIMAL_TAG_LENGTH,
    );
  });

  it("task title length matches TaskCreateIn", () => {
    expect(schemaBound(spec, "TaskCreateIn", "title", "maxLength")).toBe(
      caps.MAX_TASK_TITLE_LENGTH,
    );
  });

  it("task recurrence matches TaskCreateIn", () => {
    expect(schemaBound(spec, "TaskCreateIn", "recur_days", "maximum")).toBe(caps.MAX_RECUR_DAYS);
  });

  it("withdrawal window matches the health form's mirrored constant", () => {
    // The 730-day cap is a service-layer date rule (withdrawal_until is a
    // date on the wire, so the spec carries no numeric bound); the fence is
    // the health form importing this module instead of re-declaring 730.
    expect(caps.MAX_WITHDRAWAL_DAYS).toBe(730);
  });

  it("free-text notes length matches HealthEventIn.notes", () => {
    expect(schemaBound(spec, "HealthEventIn", "notes", "maxLength")).toBe(
      caps.MAX_FREE_TEXT_LENGTH,
    );
  });

  it("purchase batch count and age caps match PurchaseBatchIn", () => {
    expect(schemaBound(spec, "PurchaseBatchIn", "count", "maximum")).toBe(caps.MAX_BATCH_COUNT);
    expect(schemaBound(spec, "PurchaseBatchIn", "transport_hours", "maximum")).toBe(
      caps.MAX_TRANSPORT_HOURS,
    );
    // avg_age_months carries its bound as exclusive-maximum-style `le`
    // inside anyOf (pydantic Field(le=...) exports as "maximum" here only
    // for ints; the float field keeps `le`), so assert via either key.
    const batchSchemas = (spec as { components?: { schemas?: Record<string, { properties?: Record<string, unknown> }> } })
      .components?.schemas;
    const props = (batchSchemas?.PurchaseBatchIn?.properties ?? {}) as Record<
      string,
      Record<string, unknown>
    >;
    const age = props.avg_age_months;
    const anyOf = age?.anyOf as Array<Record<string, unknown>> | undefined;
    const boundFrom = (entry: Record<string, unknown> | undefined): number | null =>
      entry ? ((entry.maximum ?? entry.le ?? entry.exclusiveMaximum) as number | undefined) ?? null : null;
    const le =
      boundFrom(age) ??
      (anyOf ? (anyOf.map(boundFrom).find((v): v is number => v !== null) ?? null) : null);
    expect(le).toBe(caps.MAX_AGE_MONTHS);
  });
});

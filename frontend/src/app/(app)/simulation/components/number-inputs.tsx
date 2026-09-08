"use client";

/** Numeric field inputs for the simulation assumptions/events editors:
 *  commit-only-valid draft handling with live-bound recovery. */

import { useEffect, useRef, useState, type ComponentProps } from "react";

import { Input } from "@/components/ui/input";

import { humanize } from "./format-helpers";

export type NumericRule = {
  integer?: boolean;
  min?: number;
  max?: number;
  exclusiveMin?: number;
  unit?: string;
};

export type NumberArrayRule = {
  exactLength?: number;
  maxLength?: number;
  integer?: boolean;
  min?: number;
  max?: number;
  exclusiveMin?: number;
  allowEmpty?: boolean;
  unique?: boolean;
  nondecreasing?: boolean;
  itemLabel: string;
};

type NumberInputProps = Omit<
  ComponentProps<typeof Input>,
  "type" | "value" | "onChange" | "onBlur"
> &
  NumericRule &
  {
    onValidityChange?: (valid: boolean) => void;
  } &
  (
    | {
        value: number;
        nullable?: false;
        onCommit: (value: number) => void;
      }
    | {
        value: number | null;
        nullable: true;
        onCommit: (value: number | null) => void;
      }
  );

/** Numeric input for the assumptions/events editors: commits only finite
 *  numbers, so NaN (or a silent 0) can never reach the assumptions object.
 *  Blank leaves the stored value unchanged — or commits null when `nullable`
 *  (e.g. price per head = auto); unparseable text shows an inline error. The
 *  draft is local while the field is being edited, so external updates
 *  (defaults / scenario loads) still flow through otherwise. */
export function NumberInput(props: NumberInputProps) {
  const {
    value,
    onCommit,
    nullable = false,
    integer,
    min,
    max,
    exclusiveMin,
    unit,
    onValidityChange,
    id,
    "aria-describedby": describedBy,
    ...inputProps
  } = props;
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const errorId = id ? `${id}-error` : undefined;
  const commit = useRef(onCommit);
  useEffect(() => {
    commit.current = onCommit;
  });
  // A draft can become legal solely because a live bound changed (for
  // example, event month 90 when the horizon grows from 60 to 120). Keep the
  // value to commit after render; clearing only the error would otherwise show
  // 90 while the payload silently retained the old month.
  const [recoveredBoundsValue, setRecoveredBoundsValue] = useState<{
    value: number | null;
  } | null>(null);
  const committedBoundsRecovery = useRef<typeof recoveredBoundsValue>(null);

  function validate(raw: string): { value?: number | null; error?: string } {
    if (raw === "") {
      return nullable ? { value: null } : { error: "A value is required." };
    }
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return { error: "Enter a valid number." };
    if (integer && !Number.isInteger(parsed)) return { error: "Enter a whole number." };
    if (exclusiveMin !== undefined && parsed <= exclusiveMin)
      return { error: `Must be greater than ${exclusiveMin}.` };
    if (min !== undefined && parsed < min) return { error: `Must be at least ${min}.` };
    if (max !== undefined && parsed > max) return { error: `Must be at most ${max}.` };
    return { value: parsed };
  }

  function update(raw: string) {
    setDraft(raw);
    const next = validate(raw);
    const message = next.error ?? null;
    setError(message);
    onValidityChange?.(!message);
    if (!message) {
      if (next.value === null) (onCommit as (v: number | null) => void)(null);
      else (onCommit as (v: number) => void)(next.value as number);
    }
  }

  // Bounds can come from live state (the herd-event Month field takes
  // `max={horizonMonths}`), and validation otherwise ran only on keystroke —
  // so a bound change stranded both the error text and this field's entry in
  // the parent's `invalidFields` set, which gates Run and Save. Re-derive
  // while rendering (React's documented pattern for state that depends on
  // props) rather than in an effect.
  const boundsKey = `${min}|${max}|${exclusiveMin}|${integer}|${nullable}`;
  const [seenBounds, setSeenBounds] = useState(boundsKey);
  if (boundsKey !== seenBounds) {
    setSeenBounds(boundsKey);
    const next = validate(draft ?? (value === null ? "" : String(value)));
    setRecoveredBoundsValue(
      draft !== null && error && !next.error
        ? { value: next.value as number | null }
        : null,
    );
    setError(next.error ?? null);
  }

  useEffect(() => {
    if (
      recoveredBoundsValue === null ||
      committedBoundsRecovery.current === recoveredBoundsValue
    )
      return;
    committedBoundsRecovery.current = recoveredBoundsValue;
    if (recoveredBoundsValue.value === null)
      (commit.current as (v: number | null) => void)(null);
    else (commit.current as (v: number) => void)(recoveredBoundsValue.value);
  }, [recoveredBoundsValue]);

  // Keep the parent's invalidFields in step with `error`, however it changed.
  const notifyValidity = useRef(onValidityChange);
  useEffect(() => {
    notifyValidity.current = onValidityChange;
  });
  useEffect(() => {
    notifyValidity.current?.(!error);
  }, [error]);

  return (
    <>
      <Input
        id={id}
        data-unit={unit}
        type="number"
        step={integer ? 1 : "any"}
        min={exclusiveMin === undefined ? min : undefined}
        max={max}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={[describedBy, error ? errorId : null]
          .filter(Boolean)
          .join(" ") || undefined}
        {...inputProps}
        value={draft ?? (value === null ? "" : String(value))}
        onChange={(e) => update(e.target.value)}
        onBlur={() => {
          if (!error) setDraft(null);
        }}
      />
      {error && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </>
  );
}

export function NumberArrayInput({
  id,
  value,
  rule,
  onCommit,
  onValidityChange,
}: {
  id: string;
  value: number[];
  rule: NumberArrayRule;
  onCommit: (value: number[]) => void;
  onValidityChange: (valid: boolean) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const errorId = `${id}-error`;
  const commit = useRef(onCommit);
  useEffect(() => {
    commit.current = onCommit;
  });
  const [recoveredBoundsValue, setRecoveredBoundsValue] = useState<number[] | null>(null);
  const committedBoundsRecovery = useRef<typeof recoveredBoundsValue>(null);

  function validate(raw: string): { parsed: number[]; message: string | null } {
    if (raw.trim() === "" && rule.allowEmpty) return { parsed: [], message: null };
    const tokens = raw.split(",").map((token) => token.trim());
    let message: string | null = null;
    const parsed = tokens.map(Number);
    if (tokens.some((token) => token === "") || parsed.some((n) => !Number.isFinite(n)))
      message = "Enter only comma-separated numbers.";
    else if (rule.exactLength !== undefined && parsed.length !== rule.exactLength)
      message = `Enter exactly ${rule.exactLength} ${rule.itemLabel}.`;
    else if (rule.maxLength !== undefined && parsed.length > rule.maxLength)
      message = `Enter at most ${rule.maxLength} ${rule.itemLabel}.`;
    else if (rule.integer && parsed.some((n) => !Number.isInteger(n)))
      message = `Every ${rule.itemLabel} entry must be a whole number.`;
    else if (
      rule.exclusiveMin !== undefined &&
      parsed.some((n) => n <= rule.exclusiveMin!)
    )
      message = `Every entry must be greater than ${rule.exclusiveMin}.`;
    else if (rule.min !== undefined && parsed.some((n) => n < rule.min!))
      message = `Every entry must be at least ${rule.min}.`;
    else if (rule.max !== undefined && parsed.some((n) => n > rule.max!))
      message = `Every entry must be at most ${rule.max}.`;
    else if (rule.unique && new Set(parsed).size !== parsed.length)
      message = `${humanize(rule.itemLabel)} must not contain duplicates.`;
    else if (
      rule.nondecreasing &&
      parsed.some((n, index) => index > 0 && n < parsed[index - 1])
    )
      message = `${humanize(rule.itemLabel)} must not decrease.`;
    return { parsed, message };
  }

  function update(raw: string) {
    setDraft(raw);
    const { parsed, message } = validate(raw);
    setError(message);
    // Notify synchronously on every raw edit, not only when the derived error
    // changes. Conflict recovery uses the parent callback as an edit fence, so
    // a second keystroke in an already-invalid draft must still supersede the
    // pending refresh before it can remount this input and erase that draft.
    onValidityChange(!message);
    if (!message) onCommit(parsed);
  }

  // Some bounds are derived from live state rather than constants — notably
  // `max: horizonMonths` for sales.festival_sale_months. Validation otherwise
  // ran only on keystroke, so raising the horizon left the old error rendered
  // AND left this field in the parent's `invalidFields` set, which gates Run
  // and Save; lowering it did the reverse, letting an out-of-range payload
  // through. `rule` is rebuilt every render, so key on its primitive fields.
  const boundsKey = [
    rule.min,
    rule.max,
    rule.exclusiveMin,
    rule.exactLength,
    rule.maxLength,
    rule.integer,
    rule.unique,
    rule.nondecreasing,
    rule.allowEmpty,
  ].join("|");
  const [seenBounds, setSeenBounds] = useState(boundsKey);
  if (boundsKey !== seenBounds) {
    setSeenBounds(boundsKey);
    const next = validate(draft ?? value.join(", "));
    setRecoveredBoundsValue(
      draft !== null && error && !next.message ? next.parsed : null,
    );
    setError(next.message);
  }

  useEffect(() => {
    if (
      recoveredBoundsValue === null ||
      committedBoundsRecovery.current === recoveredBoundsValue
    )
      return;
    committedBoundsRecovery.current = recoveredBoundsValue;
    commit.current(recoveredBoundsValue);
  }, [recoveredBoundsValue]);

  // Keep the parent's invalidFields in step with `error`, however it changed.
  const notifyValidity = useRef(onValidityChange);
  useEffect(() => {
    notifyValidity.current = onValidityChange;
  });
  useEffect(() => {
    notifyValidity.current(!error);
  }, [error]);

  return (
    <>
      <Input
        id={id}
        type="text"
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={error ? errorId : undefined}
        value={draft ?? value.join(", ")}
        onChange={(event) => update(event.target.value)}
        onBlur={() => {
          if (!error) setDraft(null);
        }}
      />
      {error && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </>
  );
}

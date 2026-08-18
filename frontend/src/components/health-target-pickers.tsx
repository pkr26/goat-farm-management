"use client";

import { useState } from "react";

import {
  healthAnimalOptionsApiHealthAnimalsGet,
  healthPurchaseBatchOptionsApiHealthPurchaseBatchesGet,
  useHealthAnimalOptionsApiHealthAnimalsGet,
  useHealthPurchaseBatchOptionsApiHealthPurchaseBatchesGet,
} from "@/api/generated/endpoints";
import type {
  HealthAnimalOptionOut,
  HealthPurchaseBatchOptionOut,
} from "@/api/generated/models";
import {
  RemotePicker,
  type RemotePickerLoadArgs,
  type RemotePickerOption,
  type RemotePickerPage,
} from "@/components/remote-picker";

interface HealthTargetPickerProps {
  id: string;
  value: string;
  onValueChange: (value: string) => void;
  selectedOption?: RemotePickerOption | null;
  placeholder?: string;
  dialogTitle?: string;
  disabled?: boolean;
  className?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

function healthAnimalOption(animal: HealthAnimalOptionOut): RemotePickerOption {
  return {
    value: String(animal.id),
    label: `${animal.tag_number}${animal.name ? ` · ${animal.name}` : ""} — ${animal.current_bucket}`,
  };
}

/** Active-animal lookup authorized by health.view, including exact #id
 * resolution for task/deep-link selections outside the current result page. */
export function HealthAnimalPicker({
  id,
  value,
  onValueChange,
  selectedOption,
  placeholder = "Pick an animal",
  dialogTitle = "Choose an animal",
  disabled,
  className,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: HealthTargetPickerProps) {
  const [chosenOption, setChosenOption] = useState<RemotePickerOption | null>(null);
  const selectedId = /^\d+$/.test(value) ? Number(value) : null;
  const selectedQuery = useHealthAnimalOptionsApiHealthAnimalsGet(
    { q: selectedId === null ? undefined : `#${selectedId}`, limit: 1, offset: 0 },
    {
      query: {
        enabled:
          selectedId !== null &&
          selectedOption?.value !== value &&
          chosenOption?.value !== value,
        retry: false,
      },
    },
  );
  const resolved =
    selectedQuery.data?.status === 200
      ? selectedQuery.data.data.animals.find((animal) => animal.id === selectedId)
      : undefined;
  const resolvedOption = resolved
    ? healthAnimalOption(resolved)
    : selectedOption?.value === value
      ? selectedOption
      : null;
  const selected = chosenOption?.value === value ? chosenOption : resolvedOption;

  async function loadPage({
    query,
    offset,
    limit,
    signal,
  }: RemotePickerLoadArgs): Promise<RemotePickerPage> {
    const response = await healthAnimalOptionsApiHealthAnimalsGet(
      { q: query || undefined, limit, offset },
      { signal },
    );
    if (response.status !== 200) throw new Error("Could not load health animals.");
    return {
      options: response.data.animals.map(healthAnimalOption),
      total: response.data.total,
      nextOffset: offset + response.data.animals.length,
    };
  }

  return (
    <RemotePicker
      id={id}
      value={value}
      onValueChange={onValueChange}
      onOptionChange={setChosenOption}
      selectedOption={selected}
      placeholder={placeholder}
      dialogTitle={dialogTitle}
      dialogDescription="Search active animals by tag, name or exact #id."
      searchLabel="Search health animals"
      searchPlaceholder="Search tag, name or #animal ID…"
      searchMaxLength={60}
      emptyMessage="No active animals match this search."
      sourcePath="/api/health/animals"
      cacheKey={["health-animal-picker"]}
      loadPage={loadPage}
      disabled={disabled}
      className={className}
      aria-invalid={ariaInvalid}
      aria-describedby={ariaDescribedBy}
    />
  );
}

function healthBatchOption(batch: HealthPurchaseBatchOptionOut): RemotePickerOption {
  return {
    value: String(batch.id),
    label: `Batch #${batch.id} — ${batch.active_quarantine_animal_count} active in quarantine`,
  };
}

/** Targetable-batch lookup authorized by health.manage without purchase-ledger access. */
export function HealthPurchaseBatchPicker({
  id,
  value,
  onValueChange,
  selectedOption,
  placeholder = "Pick a purchase batch",
  dialogTitle = "Choose a purchase batch",
  disabled,
  className,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: HealthTargetPickerProps) {
  const [chosenOption, setChosenOption] = useState<RemotePickerOption | null>(null);
  const selectedId = /^\d+$/.test(value) ? Number(value) : null;
  const selectedQuery = useHealthPurchaseBatchOptionsApiHealthPurchaseBatchesGet(
    { q: selectedId === null ? undefined : `#${selectedId}`, limit: 1, offset: 0 },
    {
      query: {
        enabled:
          selectedId !== null &&
          selectedOption?.value !== value &&
          chosenOption?.value !== value,
        retry: false,
      },
    },
  );
  const resolved =
    selectedQuery.data?.status === 200
      ? selectedQuery.data.data.batches.find((batch) => batch.id === selectedId)
      : undefined;
  const resolvedOption = resolved
    ? healthBatchOption(resolved)
    : selectedOption?.value === value
      ? selectedOption
      : null;
  const selected = chosenOption?.value === value ? chosenOption : resolvedOption;

  async function loadPage({
    query,
    offset,
    limit,
    signal,
  }: RemotePickerLoadArgs): Promise<RemotePickerPage> {
    const response = await healthPurchaseBatchOptionsApiHealthPurchaseBatchesGet(
      { q: query || undefined, limit, offset },
      { signal },
    );
    if (response.status !== 200) throw new Error("Could not load health purchase batches.");
    return {
      options: response.data.batches.map(healthBatchOption),
      total: response.data.total,
      nextOffset: offset + response.data.batches.length,
    };
  }

  return (
    <RemotePicker
      id={id}
      value={value}
      onValueChange={onValueChange}
      onOptionChange={setChosenOption}
      selectedOption={selected}
      placeholder={placeholder}
      dialogTitle={dialogTitle}
      dialogDescription="Find a targetable purchase batch by its exact batch ID."
      searchLabel="Search health purchase batches"
      searchPlaceholder="Enter batch ID…"
      searchMaxLength={20}
      emptyMessage="No targetable purchase batches match this search."
      sourcePath="/api/health/purchase-batches"
      cacheKey={["health-purchase-batch-picker"]}
      loadPage={loadPage}
      disabled={disabled}
      className={className}
      aria-invalid={ariaInvalid}
      aria-describedby={ariaDescribedBy}
    />
  );
}

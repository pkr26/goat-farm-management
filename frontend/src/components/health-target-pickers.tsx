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
import { useT, type TFn } from "@/lib/i18n";

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

function healthAnimalOption(animal: HealthAnimalOptionOut, t: TFn): RemotePickerOption {
  return {
    value: String(animal.id),
    label:
      `${animal.tag_number}${animal.name ? ` · ${animal.name}` : ""} — ${animal.current_bucket}` +
      // A restricted animal stays selectable (the write is not forbidden),
      // but the operator must see the hold before choosing it (L16).
      (animal.movement_restricted ? t("picker.healthAnimal.restrictedSuffix") : ""),
  };
}

/** Active-animal lookup authorized by health.view, including exact #id
 * resolution for task/deep-link selections outside the current result page. */
export function HealthAnimalPicker({
  id,
  value,
  onValueChange,
  selectedOption,
  placeholder,
  dialogTitle,
  disabled,
  className,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: HealthTargetPickerProps) {
  const t = useT();
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
    ? healthAnimalOption(resolved, t)
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
      options: response.data.animals.map((animal) => healthAnimalOption(animal, t)),
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
      placeholder={placeholder ?? t("picker.animal.placeholder")}
      dialogTitle={dialogTitle ?? t("picker.animal.dialogTitle")}
      dialogDescription={t("picker.healthAnimal.dialogDescription")}
      searchLabel={t("picker.healthAnimal.searchLabel")}
      searchPlaceholder={t("picker.healthAnimal.searchPlaceholder")}
      searchMaxLength={60}
      emptyMessage={t("picker.healthAnimal.noMatch")}
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

function healthBatchOption(batch: HealthPurchaseBatchOptionOut, t: TFn): RemotePickerOption {
  return {
    value: String(batch.id),
    label: t("picker.batch.optionLabel", {
      id: batch.id,
      count: batch.active_quarantine_animal_count,
    }),
  };
}

/** Targetable-batch lookup authorized by health.manage without purchase-ledger access. */
export function HealthPurchaseBatchPicker({
  id,
  value,
  onValueChange,
  selectedOption,
  placeholder,
  dialogTitle,
  disabled,
  className,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: HealthTargetPickerProps) {
  const t = useT();
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
    ? healthBatchOption(resolved, t)
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
      options: response.data.batches.map((batch) => healthBatchOption(batch, t)),
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
      placeholder={placeholder ?? t("picker.batch.placeholder")}
      dialogTitle={dialogTitle ?? t("picker.batch.dialogTitle")}
      dialogDescription={t("picker.batch.dialogDescription")}
      searchLabel={t("picker.batch.searchLabel")}
      searchPlaceholder={t("picker.batch.searchPlaceholder")}
      searchMaxLength={20}
      emptyMessage={t("picker.batch.noMatch")}
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

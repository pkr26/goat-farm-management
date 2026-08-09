"use client";

import { useMemo, useState } from "react";

import {
  listAnimalsApiAnimalsGet,
  useAnimalProfileApiAnimalsAnimalIdGet,
} from "@/api/generated/endpoints";
import type { AnimalOut } from "@/api/generated/models";
import {
  RemotePicker,
  type RemotePickerLoadArgs,
  type RemotePickerOption,
  type RemotePickerPage,
} from "@/components/remote-picker";

type AnimalLabelVariant =
  | "basic"
  | "name-dash"
  | "bucket"
  | "breeding-doe"
  | "breeding-buck";

interface AnimalPickerProps {
  id: string;
  value: string;
  onValueChange: (value: string) => void;
  placeholder?: string;
  dialogTitle?: string;
  labelVariant?: AnimalLabelVariant;
  eligibleIds?: readonly number[];
  eligibilityKey?: string;
  selectedOption?: RemotePickerOption | null;
  staticOptions?: RemotePickerOption[];
  disabled?: boolean;
  className?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

function animalOption(animal: AnimalOut, variant: AnimalLabelVariant): RemotePickerOption {
  const name = animal.name ? ` · ${animal.name}` : "";
  if (variant === "name-dash") {
    return {
      value: String(animal.id),
      label: `${animal.tag_number}${animal.name ? ` — ${animal.name}` : ""}`,
    };
  }
  if (variant === "bucket") {
    return {
      value: String(animal.id),
      label: `${animal.tag_number}${name} — ${animal.current_bucket}`,
    };
  }
  if (variant === "breeding-doe") {
    const age = animal.age_months != null ? ` — ${animal.age_months} mo` : "";
    const weight =
      animal.latest_weight_kg != null
        ? `${age ? ", " : " — "}${animal.latest_weight_kg.toFixed(1)} kg`
        : "";
    return {
      value: String(animal.id),
      label: `${animal.tag_number}${name}${age}${weight}`,
    };
  }
  if (variant === "breeding-buck") {
    return {
      value: String(animal.id),
      label: `${animal.tag_number}${name}${animal.age_months != null ? ` — ${animal.age_months} mo` : ""}`,
    };
  }
  return { value: String(animal.id), label: `${animal.tag_number}${name}` };
}

/** Remote animal picker shared by every workflow that links a record to a goat. */
export function AnimalPicker({
  id,
  value,
  onValueChange,
  placeholder = "Pick an animal",
  dialogTitle = "Choose an animal",
  labelVariant = "basic",
  eligibleIds,
  eligibilityKey = "all-active",
  selectedOption,
  staticOptions,
  disabled,
  className,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: AnimalPickerProps) {
  const [chosenOption, setChosenOption] = useState<RemotePickerOption | null>(null);
  const eligibleSet = useMemo(
    () => (eligibleIds ? new Set(eligibleIds) : null),
    [eligibleIds],
  );
  const selectedAnimalId = /^\d+$/.test(value) ? Number(value) : null;
  const selectedAnimalQuery = useAnimalProfileApiAnimalsAnimalIdGet(
    selectedAnimalId ?? 0,
    undefined,
    {
      query: {
        enabled:
          selectedAnimalId !== null &&
          selectedOption?.value !== value &&
          chosenOption?.value !== value,
        retry: false,
      },
    },
  );
  const selectedAnimal =
    selectedAnimalQuery.data?.status === 200
      ? animalOption(selectedAnimalQuery.data.data.animal, labelVariant)
      : selectedOption?.value === value
        ? selectedOption
        : chosenOption?.value === value
          ? chosenOption
          : null;

  async function loadPage({
    query,
    offset,
    limit,
    signal,
  }: RemotePickerLoadArgs): Promise<RemotePickerPage> {
    const response = await listAnimalsApiAnimalsGet(
      { q: query || undefined, limit, offset },
      { signal },
    );
    if (response.status !== 200) throw new Error("Could not load animals.");
    const sourceAnimals = response.data.animals;
    return {
      options: sourceAnimals
        .filter((animal) => !eligibleSet || eligibleSet.has(animal.id))
        .map((animal) => animalOption(animal, labelVariant)),
      total: response.data.total,
      nextOffset: offset + sourceAnimals.length,
    };
  }

  return (
    <RemotePicker
      id={id}
      value={value}
      onValueChange={onValueChange}
      onOptionChange={setChosenOption}
      selectedOption={selectedAnimal}
      staticOptions={staticOptions}
      placeholder={placeholder}
      dialogTitle={dialogTitle}
      dialogDescription="Search by tag or name. Results are loaded in pages."
      searchLabel="Search animals"
      searchPlaceholder="Search tag or name…"
      searchMaxLength={60}
      emptyMessage={eligibleSet ? "No eligible animals match this search." : "No animals match this search."}
      noEligibleYetMessage="No eligible animals in the records checked yet. Load more to continue."
      sourcePath="/api/animals"
      cacheKey={["animal-picker", eligibilityKey, labelVariant]}
      loadPage={loadPage}
      disabled={disabled}
      className={className}
      aria-invalid={ariaInvalid}
      aria-describedby={ariaDescribedBy}
    />
  );
}

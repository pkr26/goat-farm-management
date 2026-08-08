"use client";

import { useMemo } from "react";

import { breedingCandidatesApiBreedingCandidatesGet } from "@/api/generated/endpoints";
import type {
  BreedingCandidateOut,
  BreedingCandidatesApiBreedingCandidatesGetKind,
} from "@/api/generated/models";
import {
  RemotePicker,
  type RemotePickerLoadArgs,
  type RemotePickerOption,
  type RemotePickerPage,
} from "@/components/remote-picker";

interface BreedingCandidatePickerProps {
  id: string;
  kind: BreedingCandidatesApiBreedingCandidatesGetKind;
  value: string;
  onValueChange: (value: string) => void;
  eligibleIds: readonly number[];
  placeholder: string;
  dialogTitle: string;
  disabled?: boolean;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

function candidateOption(
  candidate: BreedingCandidateOut,
  kind: BreedingCandidatesApiBreedingCandidatesGetKind,
): RemotePickerOption {
  const name = candidate.name ? ` · ${candidate.name}` : "";
  const age = candidate.age_months !== null ? ` — ${candidate.age_months} mo` : "";
  const weight =
    kind === "doe" && candidate.latest_weight_kg !== null
      ? `${age ? ", " : " — "}${candidate.latest_weight_kg.toFixed(1)} kg`
      : "";
  return {
    value: String(candidate.id),
    label: `${candidate.tag_number}${name}${age}${weight}`,
  };
}

/** Least-privilege selector backed by breeding.manage-scoped candidates. */
export function BreedingCandidatePicker({
  id,
  kind,
  value,
  onValueChange,
  eligibleIds,
  placeholder,
  dialogTitle,
  disabled,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: BreedingCandidatePickerProps) {
  const eligibleSet = useMemo(() => new Set(eligibleIds), [eligibleIds]);

  async function loadPage({
    query,
    offset,
    limit,
    signal,
  }: RemotePickerLoadArgs): Promise<RemotePickerPage> {
    const response = await breedingCandidatesApiBreedingCandidatesGet(
      { kind, q: query || undefined, limit, offset },
      { signal },
    );
    if (response.status !== 200) throw new Error("Could not load breeding candidates.");
    return {
      options: response.data.candidates
        .filter((candidate) => eligibleSet.has(candidate.id))
        .map((candidate) => candidateOption(candidate, kind)),
      total: response.data.total,
      nextOffset: offset + response.data.candidates.length,
    };
  }

  return (
    <RemotePicker
      id={id}
      value={value}
      onValueChange={onValueChange}
      placeholder={placeholder}
      dialogTitle={dialogTitle}
      dialogDescription="Search eligible animals by tag or name. Results are loaded in pages."
      searchLabel="Search breeding candidates"
      searchPlaceholder="Search tag or name…"
      searchMaxLength={60}
      emptyMessage={`No eligible ${kind === "doe" ? "does" : "bucks"} match this search.`}
      noEligibleYetMessage="No listed eligible animals in the records checked yet. Load more to continue."
      sourcePath="/api/breeding/candidates"
      cacheKey={["breeding-candidate-picker", kind, eligibleIds.join(",")]}
      loadPage={loadPage}
      disabled={disabled}
      aria-invalid={ariaInvalid}
      aria-describedby={ariaDescribedBy}
    />
  );
}

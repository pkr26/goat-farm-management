"use client";

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
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { useT } from "@/lib/i18n";

interface BreedingCandidatePickerProps {
  id: string;
  kind: BreedingCandidatesApiBreedingCandidatesGetKind;
  value: string;
  onValueChange: (value: string) => void;
  /** Lifted so the chosen label survives this component unmounting — the
   *  enclosing dialog keeps the form value but destroys picker state. */
  onOptionChange?: (option: RemotePickerOption) => void;
  selectedOption?: RemotePickerOption | null;
  placeholder: string;
  dialogTitle: string;
  disabled?: boolean;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

function candidateOption(
  candidate: BreedingCandidateOut,
  kind: BreedingCandidatesApiBreedingCandidatesGetKind,
  cullSuffix: string,
): RemotePickerOption {
  const name = candidate.name ? ` · ${candidate.name}` : "";
  const age = candidate.age_months !== null ? ` — ${candidate.age_months} mo` : "";
  const weight =
    kind === "doe" && candidate.latest_weight_kg !== null
      ? `${age ? ", " : " — "}${candidate.latest_weight_kg.toFixed(1)} kg`
      : "";
  // Cull-flagged does are servable by the owner only — the flag must be visible at pick
  // time so a non-owner manager skips her instead of filling the form into a guaranteed
  // 409. The suffix resolves through the i18n catalog so the "owner only" warning is
  // not English-only for Telugu managers.
  const cull = kind === "doe" && candidate.cull_candidate ? cullSuffix : "";
  return {
    value: String(candidate.id),
    label: `${candidate.tag_number}${name}${age}${weight}${cull}`,
  };
}

/** Least-privilege selector backed by breeding.manage-scoped candidates. */
export function BreedingCandidatePicker({
  id,
  kind,
  value,
  onValueChange,
  onOptionChange,
  selectedOption,
  placeholder,
  dialogTitle,
  disabled,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: BreedingCandidatePickerProps) {
  const t = useT();
  // Species nouns keep the goat vocabulary in one place.
  const vocabulary = farmVocabulary;
  const kindNoun =
    kind === "doe" ? vocabulary.femaleAdultPlural : `${vocabulary.maleAdult}s`;
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
      options: response.data.candidates.map((candidate) =>
        candidateOption(candidate, kind, t("picker.candidates.cullSuffix")),
      ),
      total: response.data.total,
      nextOffset: offset + response.data.candidates.length,
    };
  }

  return (
    <RemotePicker
      id={id}
      value={value}
      onValueChange={onValueChange}
      onOptionChange={onOptionChange}
      selectedOption={selectedOption}
      placeholder={placeholder}
      dialogTitle={dialogTitle}
      dialogDescription={t("picker.candidates.description")}
      searchLabel={t("picker.candidates.searchLabel")}
      searchPlaceholder={t("picker.candidates.searchPlaceholder")}
      searchMaxLength={60}
      emptyMessage={t("picker.candidates.emptyMessage", { kind: kindNoun })}
      noEligibleYetMessage={t("picker.candidates.noEligibleYet")}
      sourcePath="/api/breeding/candidates"
      cacheKey={["breeding-candidate-picker", kind]}
      loadPage={loadPage}
      disabled={disabled}
      aria-invalid={ariaInvalid}
      aria-describedby={ariaDescribedBy}
    />
  );
}

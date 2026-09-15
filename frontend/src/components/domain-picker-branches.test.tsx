import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Species nouns come from the session farm type; these prop-capture tests
// run outside the auth providers, so pin the GOAT vocabulary.
vi.mock("@/hooks/use-farm-type", () => ({ useFarmType: () => "GOAT" }));

import type { AnimalOut, AnimalOutCurrentBucket} from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
import { BreedingCandidatePicker } from "@/components/breeding-candidate-picker";
import {
  HealthAnimalPicker,
  HealthPurchaseBatchPicker,
} from "@/components/health-target-pickers";
import type {
  RemotePickerLoadArgs,
  RemotePickerOption,
  RemotePickerPage,
} from "@/components/remote-picker";

interface CapturedPickerProps {
  loadPage: (args: RemotePickerLoadArgs) => Promise<RemotePickerPage>;
  selectedOption?: RemotePickerOption | null;
  cacheKey: readonly unknown[];
  placeholder?: string;
  dialogTitle?: string;
  sourcePath?: string;
  emptyMessage?: string;
  onOptionChange?: (option: RemotePickerOption) => void;
}

const picker = vi.hoisted(() => ({
  current: null as CapturedPickerProps | null,
  listAnimals: vi.fn(),
  animalProfile: vi.fn(),
  breedingCandidates: vi.fn(),
  healthAnimals: vi.fn(),
  healthAnimalOptions: vi.fn(),
  healthBatches: vi.fn(),
  healthBatchOptions: vi.fn(),
}));

vi.mock("@/api/generated/endpoints", () => ({
  listAnimalsApiAnimalsGet: picker.listAnimals,
  useAnimalProfileApiAnimalsAnimalIdGet: picker.animalProfile,
  breedingCandidatesApiBreedingCandidatesGet: picker.breedingCandidates,
  healthAnimalOptionsApiHealthAnimalsGet: picker.healthAnimals,
  useHealthAnimalOptionsApiHealthAnimalsGet: picker.healthAnimalOptions,
  healthPurchaseBatchOptionsApiHealthPurchaseBatchesGet: picker.healthBatches,
  useHealthPurchaseBatchOptionsApiHealthPurchaseBatchesGet: picker.healthBatchOptions,
}));

vi.mock("@/components/remote-picker", () => ({
  RemotePicker: (props: CapturedPickerProps) => {
    picker.current = props;
    return <output data-testid="selected-option">{props.selectedOption?.label ?? "none"}</output>;
  },
}));

function animal(overrides: Partial<AnimalOut> = {}): AnimalOut {
  return {
    id: 1,
    tag_number: "G-0001",
    name: "Nila",
    breed: "Osmanabadi",
    sex: "F",
    date_of_birth: "2024-01-01",
    estimated_dob: null,
    birth_type: null,
    source: "BORN",
    dam_id: null,
    sire_id: null,
    birth_weight: null,
    current_bucket: "GROWER_FEMALE" as AnimalOutCurrentBucket,
    status: "ACTIVE",
    status_date: null,
    sale_price: null,
    sale_weight_kg: null,
    buyer_name: null,
    mortality_cause_code: null,
    disposal_method: null,
    necropsy_done: false,
    necropsy_findings: null,
    coat_color: null,
    horned: null,
    purchase_date: null,
    purchase_price: null,
    seller_name: null,
    cull_candidate: false,
    movement_restricted: false,
    restriction_reason: null,
    suspected_scheduled_disease: false,
    suspected_disease: null,
    authority_notified_at: null,
    restriction_cleared_at: null,
    restriction_cleared_by_id: null,
    restriction_clearance_reference: null,
    restriction_version: 0,
    mortality_cause: null,
    mortality_reported_at: null,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    age_months: 18,
    latest_weight_kg: 24.46,
    ...overrides,
  };
}

function loadArgs(query = ""): RemotePickerLoadArgs {
  return {
    query,
    offset: 20,
    limit: 10,
    signal: new AbortController().signal,
  };
}

function captured(): CapturedPickerProps {
  if (!picker.current) throw new Error("RemotePicker props were not captured");
  return picker.current;
}

describe("AnimalPicker branches", () => {
  beforeEach(() => {
    picker.current = null;
    picker.listAnimals.mockReset();
    picker.animalProfile.mockReset();
    picker.animalProfile.mockReturnValue({ data: undefined });
  });

  it.each([
    { variant: "basic" as const, expected: "G-0001 · Nila" },
    { variant: "name-dash" as const, expected: "G-0001 — Nila" },
    { variant: "bucket" as const, expected: "G-0001 · Nila — GROWER_FEMALE" },
    { variant: "breeding-doe" as const, expected: "G-0001 · Nila — 18 mo, 24.5 kg" },
    { variant: "breeding-buck" as const, expected: "G-0001 · Nila — 18 mo" },
  ])("formats the $variant label", async ({ variant, expected }) => {
    picker.listAnimals.mockResolvedValue({
      status: 200,
      data: { animals: [animal()], total: 31 },
    });
    render(
      <AnimalPicker
        id="animal"
        value=""
        onValueChange={() => undefined}
        labelVariant={variant}
      />,
    );

    const args = loadArgs("Nila");
    await expect(captured().loadPage(args)).resolves.toEqual({
      options: [{ value: "1", label: expected }],
      total: 31,
      nextOffset: 21,
    });
    expect(picker.listAnimals).toHaveBeenCalledWith(
      { q: "Nila", limit: 10, offset: 20 },
      { signal: args.signal },
    );
  });

  it("formats absent optional animal fields without dangling punctuation", async () => {
    picker.listAnimals.mockResolvedValue({
      status: 200,
      data: {
        animals: [
          animal({ name: null, age_months: null, latest_weight_kg: 24.46 }),
          animal({ id: 2, tag_number: "G-0002", name: null, age_months: null, latest_weight_kg: null }),
        ],
        total: 2,
      },
    });
    const { rerender } = render(
      <AnimalPicker
        id="animal"
        value=""
        onValueChange={() => undefined}
        labelVariant="breeding-doe"
      />,
    );

    await expect(captured().loadPage(loadArgs())).resolves.toMatchObject({
      options: [
        { label: "G-0001 — 24.5 kg" },
        { label: "G-0002" },
      ],
    });

    rerender(
      <AnimalPicker
        id="animal"
        value=""
        onValueChange={() => undefined}
        labelVariant="breeding-buck"
      />,
    );
    await expect(captured().loadPage(loadArgs())).resolves.toMatchObject({
      options: [{ label: "G-0001" }, { label: "G-0002" }],
    });

    rerender(
      <AnimalPicker
        id="animal"
        value=""
        onValueChange={() => undefined}
        labelVariant="name-dash"
      />,
    );
    await expect(captured().loadPage(loadArgs())).resolves.toMatchObject({
      options: [{ label: "G-0001" }, { label: "G-0002" }],
    });
  });

  it("filters eligible IDs while retaining source pagination metadata", async () => {
    picker.listAnimals.mockResolvedValue({
      status: 200,
      data: { animals: [animal(), animal({ id: 2, tag_number: "G-0002" })], total: 50 },
    });
    render(
      <AnimalPicker
        id="animal"
        value=""
        onValueChange={() => undefined}
        eligibleIds={[2]}
      />,
    );

    await expect(captured().loadPage(loadArgs())).resolves.toMatchObject({
      options: [{ value: "2" }],
      total: 50,
      nextOffset: 22,
    });
    expect(captured().emptyMessage).toBe("No eligible animals match this search.");
    expect(captured().cacheKey).toEqual(["animal-picker", [2], "basic"]);
  });

  it("canonicalizes default eligibility cache keys and honors explicit revisions", () => {
    const { rerender } = render(
      <AnimalPicker
        id="animal"
        value=""
        onValueChange={() => undefined}
        eligibleIds={[3, 1, 2]}
      />,
    );
    expect(captured().cacheKey).toEqual(["animal-picker", [1, 2, 3], "basic"]);

    rerender(
      <AnimalPicker
        id="animal"
        value=""
        onValueChange={() => undefined}
        eligibleIds={[3, 1, 2]}
        eligibilityKey="revision-7"
      />,
    );
    expect(captured().cacheKey).toEqual(["animal-picker", "revision-7", "basic"]);

    rerender(<AnimalPicker id="animal" value="" onValueChange={() => undefined} />);
    expect(captured()).toMatchObject({
      cacheKey: ["animal-picker", "all-active", "basic"],
      placeholder: "Pick an animal",
      dialogTitle: "Choose an animal",
      emptyMessage: "No animals match this search.",
      sourcePath: "/api/animals",
    });
  });

  it("resolves a selected numeric ID and avoids redundant exact lookups", () => {
    picker.animalProfile.mockReturnValue({
      data: { status: 200, data: { animal: animal() } },
    });
    const selected = { value: "1", label: "Existing label" };
    const { rerender } = render(
      <AnimalPicker id="animal" value="1" onValueChange={() => undefined} />,
    );

    expect(screen.getByTestId("selected-option")).toHaveTextContent("G-0001 · Nila");
    expect(picker.animalProfile).toHaveBeenLastCalledWith(
      1,
      undefined,
      { query: { enabled: true, retry: false } },
    );

    rerender(
      <AnimalPicker
        id="animal"
        value="1"
        onValueChange={() => undefined}
        selectedOption={selected}
      />,
    );
    expect(picker.animalProfile).toHaveBeenLastCalledWith(
      1,
      undefined,
      { query: { enabled: false, retry: false } },
    );
    expect(screen.getByTestId("selected-option")).toHaveTextContent("G-0001 · Nila");
  });

  it("keeps a newly chosen option and disables its redundant exact lookup", () => {
    const { rerender } = render(
      <AnimalPicker id="animal" value="" onValueChange={() => undefined} />,
    );
    act(() => captured().onOptionChange?.({ value: "7", label: "G-0007 · Chosen" }));
    rerender(<AnimalPicker id="animal" value="7" onValueChange={() => undefined} />);

    expect(screen.getByTestId("selected-option")).toHaveTextContent("G-0007 · Chosen");
    expect(picker.animalProfile).toHaveBeenLastCalledWith(
      7,
      undefined,
      { query: { enabled: false, retry: false } },
    );
  });

  it("keeps a fresh choice ahead of an older exact-profile cache entry", () => {
    picker.animalProfile.mockReturnValue({
      data: {
        status: 200,
        data: { animal: animal({ name: "Cached old name" }) },
      },
    });
    render(<AnimalPicker id="animal" value="1" onValueChange={() => undefined} />);

    act(() => captured().onOptionChange?.({ value: "1", label: "G-0001 · Fresh choice" }));

    expect(screen.getByTestId("selected-option")).toHaveTextContent(
      "G-0001 · Fresh choice",
    );
    expect(picker.animalProfile).toHaveBeenLastCalledWith(
      1,
      undefined,
      { query: { enabled: false, retry: false } },
    );
  });

  it("uses matching supplied metadata when the exact animal lookup has no result", () => {
    const selected = { value: "4", label: "G-0004 · Supplied" };
    picker.animalProfile.mockReturnValue({ data: undefined });

    render(
      <AnimalPicker
        id="animal"
        value="4"
        onValueChange={() => undefined}
        selectedOption={selected}
      />,
    );

    expect(screen.getByTestId("selected-option")).toHaveTextContent("G-0004 · Supplied");
    expect(picker.animalProfile).toHaveBeenLastCalledWith(
      4,
      undefined,
      { query: { enabled: false, retry: false } },
    );
  });

  it("accepts only an entirely numeric selected ID", () => {
    const { rerender } = render(
      <AnimalPicker id="animal" value="12" onValueChange={() => undefined} />,
    );
    expect(picker.animalProfile).toHaveBeenLastCalledWith(
      12,
      undefined,
      { query: { enabled: true, retry: false } },
    );

    for (const invalid of ["x12", "12x"]) {
      rerender(<AnimalPicker id="animal" value={invalid} onValueChange={() => undefined} />);
      expect(picker.animalProfile).toHaveBeenLastCalledWith(
        0,
        undefined,
        { query: { enabled: false, retry: false } },
      );
    }
  });

  it("ignores supplied and chosen labels that belong to another value", () => {
    const { rerender } = render(
      <AnimalPicker
        id="animal"
        value="1"
        onValueChange={() => undefined}
        selectedOption={{ value: "2", label: "Wrong supplied option" }}
      />,
    );
    expect(screen.getByTestId("selected-option")).toHaveTextContent("none");

    act(() => captured().onOptionChange?.({ value: "7", label: "Wrong chosen option" }));
    rerender(<AnimalPicker id="animal" value="8" onValueChange={() => undefined} />);
    expect(screen.getByTestId("selected-option")).toHaveTextContent("none");
  });

  it("rejects a non-success animal page", async () => {
    picker.listAnimals.mockResolvedValue({ status: 500 });
    render(<AnimalPicker id="animal" value="" onValueChange={() => undefined} />);

    await expect(captured().loadPage(loadArgs())).rejects.toThrow("Could not load animals.");
  });
});

describe("BreedingCandidatePicker branches", () => {
  beforeEach(() => {
    picker.current = null;
    picker.breedingCandidates.mockReset();
  });

  it.each([
    { kind: "doe" as const, expected: "B-1 · Bela — 16 mo, 23.5 kg" },
    { kind: "buck" as const, expected: "B-1 · Bela — 16 mo" },
  ])("formats and requests a $kind candidate", async ({ kind, expected }) => {
    picker.breedingCandidates.mockResolvedValue({
      status: 200,
      data: {
        candidates: [
          { id: 1, tag_number: "B-1", name: "Bela", age_months: 16, latest_weight_kg: 23.46 },
        ],
        total: 1,
      },
    });
    render(
      <BreedingCandidatePicker
        id="candidate"
        kind={kind}
        value=""
        onValueChange={() => undefined}
        placeholder="Choose"
        dialogTitle="Choose candidate"
      />,
    );
    const args = loadArgs();

    await expect(captured().loadPage(args)).resolves.toEqual({
      options: [{ value: "1", label: expected }],
      total: 1,
      nextOffset: 21,
    });
    expect(picker.breedingCandidates).toHaveBeenCalledWith(
      { kind, q: undefined, limit: 10, offset: 20 },
      { signal: args.signal },
    );
  });

  it("handles absent candidate fields and rejects a non-success response", async () => {
    picker.breedingCandidates.mockResolvedValueOnce({
      status: 200,
      data: {
        candidates: [{ id: 2, tag_number: "B-2", name: null, age_months: null, latest_weight_kg: 20 }],
        total: 1,
      },
    });
    render(
      <BreedingCandidatePicker
        id="candidate"
        kind="doe"
        value=""
        onValueChange={() => undefined}
        placeholder="Choose"
        dialogTitle="Choose candidate"
      />,
    );
    await expect(captured().loadPage(loadArgs())).resolves.toMatchObject({
      options: [{ label: "B-2 — 20.0 kg" }],
    });

    picker.breedingCandidates.mockResolvedValueOnce({
      status: 200,
      data: {
        candidates: [{ id: 3, tag_number: "B-3", name: null, age_months: null, latest_weight_kg: null }],
        total: 1,
      },
    });
    await expect(captured().loadPage(loadArgs())).resolves.toMatchObject({
      options: [{ label: "B-3" }],
    });

    picker.breedingCandidates.mockResolvedValueOnce({ status: 403 });
    await expect(captured().loadPage(loadArgs())).rejects.toThrow(
      "Could not load breeding candidates.",
    );
  });

  it("uses kind-specific empty messages and cache keys", () => {
    const { rerender } = render(
      <BreedingCandidatePicker
        id="candidate"
        kind="doe"
        value=""
        onValueChange={() => undefined}
        placeholder="Choose"
        dialogTitle="Choose candidate"
      />,
    );
    expect(captured()).toMatchObject({
      emptyMessage: "No eligible does match this search.",
      cacheKey: ["breeding-candidate-picker", "doe"],
      sourcePath: "/api/breeding/candidates",
    });

    rerender(
      <BreedingCandidatePicker
        id="candidate"
        kind="buck"
        value=""
        onValueChange={() => undefined}
        placeholder="Choose"
        dialogTitle="Choose candidate"
      />,
    );
    expect(captured()).toMatchObject({
      emptyMessage: "No eligible bucks match this search.",
      cacheKey: ["breeding-candidate-picker", "buck"],
    });
  });
});

describe("health target picker branches", () => {
  beforeEach(() => {
    picker.current = null;
    picker.healthAnimals.mockReset();
    picker.healthAnimalOptions.mockReset();
    picker.healthBatches.mockReset();
    picker.healthBatchOptions.mockReset();
    picker.healthAnimalOptions.mockReturnValue({ data: undefined });
    picker.healthBatchOptions.mockReturnValue({ data: undefined });
  });

  it("formats health animal pages and resolves the exact selected animal", async () => {
    picker.healthAnimals.mockResolvedValue({
      status: 200,
      data: {
        animals: [
          { id: 1, tag_number: "H-1", name: null, current_bucket: "QUARANTINE" },
        ],
        total: 1,
      },
    });
    picker.healthAnimalOptions.mockReturnValue({
      data: {
        status: 200,
        data: {
          animals: [
            { id: 8, tag_number: "H-8", name: "Wrong", current_bucket: "DOE" },
            { id: 9, tag_number: "H-9", name: "Tara", current_bucket: "DOE" },
          ],
        },
      },
    });
    render(<HealthAnimalPicker id="health" value="9" onValueChange={() => undefined} />);

    expect(screen.getByTestId("selected-option")).toHaveTextContent("H-9 · Tara — DOE");
    expect(picker.healthAnimalOptions).toHaveBeenCalledWith(
      { q: "#9", limit: 1, offset: 0 },
      { query: { enabled: true, retry: false } },
    );
    const args = loadArgs("H-1");
    await expect(captured().loadPage(args)).resolves.toEqual({
      options: [{ value: "1", label: "H-1 — QUARANTINE" }],
      total: 1,
      nextOffset: 21,
    });
    expect(picker.healthAnimals).toHaveBeenCalledWith(
      { q: "H-1", limit: 10, offset: 20 },
      { signal: args.signal },
    );
    expect(captured()).toMatchObject({
      placeholder: "Pick an animal",
      dialogTitle: "Choose an animal",
      cacheKey: ["health-animal-picker"],
      sourcePath: "/api/health/animals",
    });
  });

  it("uses a supplied health-animal label and rejects a failed page", async () => {
    const selected = { value: "9", label: "Saved animal #9" };
    render(
      <HealthAnimalPicker
        id="health"
        value="9"
        onValueChange={() => undefined}
        selectedOption={selected}
      />,
    );
    expect(screen.getByTestId("selected-option")).toHaveTextContent("Saved animal #9");
    expect(picker.healthAnimalOptions).toHaveBeenCalledWith(
      { q: "#9", limit: 1, offset: 0 },
      { query: { enabled: false, retry: false } },
    );

    picker.healthAnimals.mockResolvedValue({ status: 403 });
    await expect(captured().loadPage(loadArgs())).rejects.toThrow(
      "Could not load health animals.",
    );
  });

  it("accepts only an entirely numeric health-animal ID", () => {
    const { rerender } = render(
      <HealthAnimalPicker id="health" value="12" onValueChange={() => undefined} />,
    );
    expect(picker.healthAnimalOptions).toHaveBeenLastCalledWith(
      { q: "#12", limit: 1, offset: 0 },
      { query: { enabled: true, retry: false } },
    );

    for (const invalid of ["x12", "12x"]) {
      rerender(
        <HealthAnimalPicker id="health" value={invalid} onValueChange={() => undefined} />,
      );
      expect(picker.healthAnimalOptions).toHaveBeenLastCalledWith(
        { q: undefined, limit: 1, offset: 0 },
        { query: { enabled: false, retry: false } },
      );
    }
  });

  it("keeps only health-animal labels that match the current value", () => {
    const { rerender } = render(
      <HealthAnimalPicker
        id="health"
        value="9"
        onValueChange={() => undefined}
        selectedOption={{ value: "8", label: "Wrong supplied animal" }}
      />,
    );
    expect(screen.getByTestId("selected-option")).toHaveTextContent("none");

    act(() => captured().onOptionChange?.({ value: "7", label: "Chosen health animal" }));
    picker.healthAnimalOptions.mockReturnValue({
      data: {
        status: 200,
        data: {
          animals: [{ id: 7, tag_number: "H-7", name: "Cached", current_bucket: "DOE" }],
        },
      },
    });
    rerender(<HealthAnimalPicker id="health" value="7" onValueChange={() => undefined} />);
    expect(screen.getByTestId("selected-option")).toHaveTextContent("Chosen health animal");
    expect(picker.healthAnimalOptions).toHaveBeenLastCalledWith(
      { q: "#7", limit: 1, offset: 0 },
      { query: { enabled: false, retry: false } },
    );

    rerender(<HealthAnimalPicker id="health" value="6" onValueChange={() => undefined} />);
    expect(screen.getByTestId("selected-option")).toHaveTextContent("none");
  });

  it("formats purchase batches and resolves only the exact selected batch", async () => {
    picker.healthBatches.mockResolvedValue({
      status: 200,
      data: { batches: [{ id: 4, active_quarantine_animal_count: 2 }], total: 1 },
    });
    picker.healthBatchOptions.mockReturnValue({
      data: {
        status: 200,
        data: {
          batches: [
            { id: 4, active_quarantine_animal_count: 99 },
            { id: 5, active_quarantine_animal_count: 3 },
          ],
        },
      },
    });
    render(<HealthPurchaseBatchPicker id="batch" value="5" onValueChange={() => undefined} />);

    expect(screen.getByTestId("selected-option")).toHaveTextContent(
      "Batch #5 — 3 active in quarantine",
    );
    expect(picker.healthBatchOptions).toHaveBeenCalledWith(
      { q: "#5", limit: 1, offset: 0 },
      { query: { enabled: true, retry: false } },
    );
    const args = loadArgs("#4");
    await expect(captured().loadPage(args)).resolves.toEqual({
      options: [{ value: "4", label: "Batch #4 — 2 active in quarantine" }],
      total: 1,
      nextOffset: 21,
    });
    expect(picker.healthBatches).toHaveBeenCalledWith(
      { q: "#4", limit: 10, offset: 20 },
      { signal: args.signal },
    );
    expect(captured()).toMatchObject({
      placeholder: "Pick a purchase batch",
      dialogTitle: "Choose a purchase batch",
      cacheKey: ["health-purchase-batch-picker"],
      sourcePath: "/api/health/purchase-batches",
    });

    picker.healthBatches.mockResolvedValue({ status: 500 });
    await expect(captured().loadPage(loadArgs())).rejects.toThrow(
      "Could not load health purchase batches.",
    );
  });

  it("validates batch IDs and keeps only matching supplied and chosen labels", () => {
    const selected = { value: "12", label: "Saved batch #12" };
    const { rerender } = render(
      <HealthPurchaseBatchPicker
        id="batch"
        value="12"
        onValueChange={() => undefined}
        selectedOption={selected}
      />,
    );
    expect(screen.getByTestId("selected-option")).toHaveTextContent("Saved batch #12");
    expect(picker.healthBatchOptions).toHaveBeenLastCalledWith(
      { q: "#12", limit: 1, offset: 0 },
      { query: { enabled: false, retry: false } },
    );

    rerender(
      <HealthPurchaseBatchPicker
        id="batch"
        value="13"
        onValueChange={() => undefined}
        selectedOption={selected}
      />,
    );
    expect(screen.getByTestId("selected-option")).toHaveTextContent("none");
    expect(picker.healthBatchOptions).toHaveBeenLastCalledWith(
      { q: "#13", limit: 1, offset: 0 },
      { query: { enabled: true, retry: false } },
    );

    act(() => captured().onOptionChange?.({ value: "14", label: "Chosen batch #14" }));
    picker.healthBatchOptions.mockReturnValue({
      data: {
        status: 200,
        data: { batches: [{ id: 14, active_quarantine_animal_count: 99 }] },
      },
    });
    rerender(<HealthPurchaseBatchPicker id="batch" value="14" onValueChange={() => undefined} />);
    expect(screen.getByTestId("selected-option")).toHaveTextContent("Chosen batch #14");
    expect(picker.healthBatchOptions).toHaveBeenLastCalledWith(
      { q: "#14", limit: 1, offset: 0 },
      { query: { enabled: false, retry: false } },
    );

    for (const invalid of ["x14", "14x"]) {
      rerender(
        <HealthPurchaseBatchPicker id="batch" value={invalid} onValueChange={() => undefined} />,
      );
      expect(picker.healthBatchOptions).toHaveBeenLastCalledWith(
        { q: undefined, limit: 1, offset: 0 },
        { query: { enabled: false, retry: false } },
      );
    }
  });
});

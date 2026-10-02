/**
 * Health page i18n: the record-event dialog (the most worker-facing form in
 * the app) renders fully in Telugu when the language is te — dialog title,
 * scope radios, field labels, the Advanced section and the zod validation
 * messages all resolve through the health.* catalog keys. Also pins that the
 * catalog's money-floor message stays byte-identical to the shared
 * MIN_PERSISTED_MONEY_MESSAGE constant other forms use.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { HealthEventOut, TaskOut } from "@/api/generated/models";
import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { MIN_PERSISTED_MONEY_MESSAGE } from "@/lib/persisted-numbers";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import HealthPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/health",
  useSearchParams: () => new URLSearchParams(window.location.search),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

beforeEach(() => {
  server.use(
    http.get("/api/health/events", () =>
      HttpResponse.json({ events: [], total: 0, limit: 50, offset: 0 }),
    ),
    http.get("/api/health/animals", () =>
      HttpResponse.json({ animals: [], total: 0, limit: 50, offset: 0 }),
    ),
    http.get("/api/tasks", () =>
      HttpResponse.json({
        today: [],
        overdue: [],
        upcoming: [],
        awaiting: [],
        completed: [],
        completed_total: 0,
        completed_limit: 50,
        completed_offset: 0,
      }),
    ),
    http.get("/api/health/schedule-templates", () =>
      HttpResponse.json({ templates: [] }),
    ),
  );
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
  window.history.replaceState({}, "", "/health");
});

async function openDialogInTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  renderWithProviders(
    <LanguageProvider>
      <HealthPage />
    </LanguageProvider>,
  );
  // Page chrome and the dialog both render in Telugu now.
  await screen.findByText("నమోదుల చిట్టా");
  await user.click(await screen.findByRole("button", { name: "నమోదు చేర్చు" }));
  const dialog = await screen.findByRole("dialog", { name: "ఆరోగ్య నమోదు చేర్చు" });
  return { user, dialog };
}

describe("HealthPage record dialog — Telugu", () => {
  it("renders the scope radios, field labels and Advanced section in Telugu", async () => {
    const { dialog } = await openDialogInTelugu();

    expect(within(dialog).getByRole("radio", { name: "ఒకే మేక" })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "మొత్తం పెంట" })).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: "కొనుగోలు బ్యాచ్" })).toBeInTheDocument();
    // Required single-animal picker and the ordinary text fields.
    expect(within(dialog).getByText("మేక *")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("మందు పేరు")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("మోతాదు")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("వెట్ (పశు వైద్యుడు)")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("గమనికలు")).toBeInTheDocument();
    // Advanced section heading + helper line.
    expect(within(dialog).getByText("అదనపు ట్రేసబిలిటీ & నిబంధనలు")).toBeInTheDocument();
    // The save action itself.
    expect(
      within(dialog).getByRole("button", { name: "నమోదు సేవ్ చేయండి" }),
    ).toBeInTheDocument();
    // No stray English label survives on the form's key fields.
    expect(within(dialog).queryByText("Animal *")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Dose")).not.toBeInTheDocument();
  });

  it("shows zod validation messages in Telugu", async () => {
    const { user, dialog } = await openDialogInTelugu();

    await user.click(within(dialog).getByRole("button", { name: "నమోదు సేవ్ చేయండి" }));

    // The picker placeholder shares the wording, so pin the error node by id.
    const error = await within(dialog).findByText("మేకను ఎంచుకోండి", {
      selector: "p[role='alert']",
    });
    expect(error).toHaveAttribute("id", "event-animal-error");
  });

  it("keeps the Telugu catalog at parity for the dialog keys", () => {
    // Spot-check key translations resolve to Telugu, never the raw key or
    // the English fallback.
    expect(translate("te", "health.form.title")).toBe("ఆరోగ్య నమోదు చేర్చు");
    expect(translate("te", "health.form.withdrawalLabel")).toBe("విత్‌డ్రాయల్ ముగింపు తేదీ");
    expect(translate("te", "health.form.isolationStartedLabel")).toBe("ఐసోలేషన్ ప్రారంభ తేదీ");
    expect(translate("te", "health.validation.withdrawalTooLong", { days: 365 })).toBe(
      "విత్‌డ్రాయల్ తేదీ నమోదు తర్వాత 365 రోజులు మించకూడదు",
    );
  });

  it("keeps the catalog money floor identical to the shared persisted-money constant", () => {
    expect(translate("en", "health.validation.moneyMin")).toBe(MIN_PERSISTED_MONEY_MESSAGE);
  });
});

/** Minimal full-shape fixtures for the log-fragment tests below. */
function makeEvent(overrides: Partial<HealthEventOut>): HealthEventOut {
  return {
    id: 1,
    animal_id: 3,
    purchase_batch_id: null,
    date: "2026-07-15",
    type: "VACCINE",
    product_name: "PPR vaccine",
    disease_target: "PPR",
    dose: "1 ml",
    route: "SC",
    vet_name: "Dr. Patil",
    cost: 1250.5,
    next_due_date: "2027-07-15",
    schedule_template_name: null,
    next_due_authority: null,
    product_lot: null,
    product_manufactured_on: null,
    product_expires_on: null,
    vaccine_valid_until: null,
    certificate_number: null,
    official_tag_number: null,
    administered_by: null,
    withdrawal_until: null,
    suspected_scheduled_disease: false,
    authority_notified_at: null,
    isolation_started_at: null,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    animal_tag: "G-003",
    ...overrides,
  };
}

function makeTask(overrides: Partial<TaskOut>): TaskOut {
  const today = new Date().toISOString().slice(0, 10);
  return {
    id: 1,
    title: "Herd vaccination round",
    title_key: null,
    title_args: {},
    due_date: today,
    status: "PENDING",
    // The dialog's linked-duty filter only accepts vaccine/deworming duties
    // that are already due.
    category: "VACCINE",
    auto_generated: false,
    animal_id: null,
    purchase_batch_id: null,
    breeding_record_id: null,
    assigned_role_id: null,
    assigned_user_id: null,
    recur_days: null,
    recurring_series_id: null,
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
    skipped_by_id: null,
    skipped_at: null,
    skip_reason: null,
    rejected_by_id: null,
    rejected_at: null,
    created_at: "2026-01-01T00:00:00Z",
    created_by_id: null,
    action_url: null,
    ...overrides,
  };
}

describe("HealthPage log and dialog fragments — Telugu", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("labels administration routes and batch/bucket targets in Telugu (2026-10-01 audit, 05-2/05-3)", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({
          events: [
            makeEvent({ id: 1, route: "INTRANASAL" }),
            makeEvent({ id: 2, animal_id: null, animal_tag: null, purchase_batch_id: 7 }),
            makeEvent({ id: 3, animal_id: null, animal_tag: null, purchase_batch_id: null }),
          ],
          total: 3,
          limit: 50,
          offset: 0,
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <LanguageProvider>
        <HealthPage />
      </LanguageProvider>,
    );

    await screen.findByText("నమోదుల చిట్టా");
    // The mobile card list repeats the target label — scope to the table.
    const table = screen.getByRole("table");
    expect(await within(table).findByText("ముక్కు ద్వారా")).toBeInTheDocument();
    expect(within(table).getByText("బ్యాచ్ #7")).toBeInTheDocument();
    expect(within(table).getByText("పెంట మొత్తం")).toBeInTheDocument();
    // Raw wire codes and the old English fragments never survive.
    expect(within(table).queryByText("INTRANASAL")).not.toBeInTheDocument();
    expect(within(table).queryByText("batch #7")).not.toBeInTheDocument();
    expect(within(table).queryByText("bucket-wide")).not.toBeInTheDocument();
    // The record dialog's route select offers the same Telugu family.
    await user.click(await screen.findByRole("button", { name: "నమోదు చేర్చు" }));
    const dialog = await screen.findByRole("dialog", { name: "ఆరోగ్య నమోదు చేర్చు" });
    await user.click(within(dialog).getByRole("combobox", { name: "ఇచ్చిన పద్ధతి" }));
    expect(await screen.findByRole("option", { name: "ముక్కు ద్వారా" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "INTRANASAL" })).not.toBeInTheDocument();
  });

  it("suffixes a linked duty's due date in Telugu (2026-10-01 audit, 05-3)", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [makeTask({ id: 9 })],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          completed_total: 0,
          completed_limit: 50,
          completed_offset: 0,
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <LanguageProvider>
        <HealthPage />
      </LanguageProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "నమోదు చేర్చు" }));
    const dialog = await screen.findByRole("dialog", { name: "ఆరోగ్య నమోదు చేర్చు" });
    await user.click(
      within(dialog).getByRole("combobox", {
        name: "లింక్ చేసిన పని (దాన్ని పూర్తి చేస్తుంది)",
      }),
    );
    // The option carries the catalog's Telugu due suffix, not "(due …)".
    expect(await screen.findByRole("option", { name: /గడువు/ })).toHaveTextContent(
      /హర్డ్ టీకా రౌండ్|Herd vaccination round/,
    );
    expect(screen.queryByRole("option", { name: /\(due / })).not.toBeInTheDocument();
  });

  it("prints the reviewed target age in Telugu months (2026-10-01 audit, 05-3)", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    server.use(
      http.post("/api/health/events/preview", () =>
        HttpResponse.json({
          scope: "bucket",
          bucket: "BREEDING",
          purchase_batch_id: null,
          task_id: null,
          target_animal_ids: [3],
          target_animals: [{ id: 3, tag_number: "G-003", name: null }],
          target_animal_ages_months: [7],
          target_count: 1,
          max_targets: 250,
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <LanguageProvider>
        <HealthPage />
      </LanguageProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "నమోదు చేర్చు" }));
    const dialog = await screen.findByRole("dialog", { name: "ఆరోగ్య నమోదు చేర్చు" });
    await user.click(within(dialog).getByRole("radio", { name: "మొత్తం పెంట" }));
    await user.click(within(dialog).getByRole("combobox", { name: "పెంట *" }));
    await user.click(await screen.findByRole("option", { name: "సంతానోత్పత్తి" }));
    await user.click(within(dialog).getByRole("button", { name: "లక్ష్య మేకలను సమీక్షించండి" }));

    // The bulk-review list states the animal's age through the catalog's
    // Telugu months token, never the hardcoded "7 mo".
    expect(await within(dialog).findByText(/7 నెలలు/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/7 mo/)).not.toBeInTheDocument();
  });
});

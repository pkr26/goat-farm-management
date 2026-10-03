import { describe, expect, it } from "vitest";

import type { CalibrationEvidence, MetricExplanation } from "@/api/generated/models";
import { translate, type TFn } from "@/lib/i18n";
import { calibrationEvidenceText, calibrationWarningText, metricExplanationText } from "./simulation-evidence-text";

const te: TFn = (key, vars) => translate("te", key, vars);
const en: TFn = (key, vars) => translate("en", key, vars);
function evidence(method: string, source = "transactions/animals"): CalibrationEvidence {
  return { path: "costs.vet_per_animal_per_year", previous_value: 250, calibrated_value: 600,
    sample_size: 18, confidence: "medium", method, source, period_start: "2026-01-01", period_end: "2026-10-03" };
}
function metric(key: string, figures: MetricExplanation["figures"] = {}): MetricExplanation {
  return { key, title: "Server-written English title", explanation: "Server-written English paragraph with ₹5.50 lakh and 12.5%.", figures };
}

describe("simulation evidence text language boundary", () => {
  it.each([
    "project_cost", "loan_amount", "subsidy_amount", "equity", "npv", "mirr", "irr", "bcr",
    "avg_dscr", "min_dscr", "peak_capacity_head", "terminal_value", "tax_total",
    "accounting_profit_total", "minimum_cash_balance", "operating_margin", "payback_month", "break_even_meat_price_per_kg",
  ])("renders %s calculation basis in Telugu while retaining the run quantities", (key) => {
    const entry = metric(key);
    const text = metricExplanationText(entry, te, "te");
    expect(text.title).toMatch(/[\u0c00-\u0c7f]/);
    expect(text.explanation).toMatch(/[\u0c00-\u0c7f]/);
    expect(text.explanation).toContain("₹5.50 లక్షలు");
    expect(text.explanation).toContain("12.5%");
    expect(text.explanation).not.toContain("Server-written English");
    expect(text.explanation).not.toContain("simulation.evidence.");
    expect(entry.explanation).toContain("Server-written English");
  });

  it("preserves full English narratives, evidence and warnings verbatim", () => {
    const entry = metric("loan_amount");
    const item = evidence("An exact English calculation method");
    expect(metricExplanationText(entry, en, "en")).toEqual({ title: entry.title, explanation: entry.explanation });
    expect(calibrationEvidenceText(item, en, "en")).toEqual({ source: item.source, method: item.method });
    expect(calibrationWarningText("A precise server warning", en, "en")).toBe("A precise server warning");
  });

  it("retains the loan balloon warning and amount absent from structured figures", () => {
    const entry = metric("loan_amount", { loan_term_months: 72, moratorium_months: 6 });
    entry.explanation = "The 60-month projection ends first, so the ₹3.20 lakh still outstanding is charged as a single balloon repayment in the final month.";
    const text = metricExplanationText(entry, te, "te");
    expect(text.explanation).toContain("చివరి నెలలో పెద్ద ఏకమొత్తంగా చెల్లించాలి");
    expect(text.explanation).toContain("₹3.20 లక్షలు");
    expect(text.explanation).toContain("60");
    expect(entry.figures).toEqual({ loan_term_months: 72, moratorium_months: 6 });
  });

  it("distinguishes omitted break-even search from an unsuccessful search", () => {
    const skipped = metric("break_even_meat_price_per_kg", { break_even_meat_price_per_kg: null });
    skipped.explanation = "The break-even meat price was not computed for this run.";
    const failed = { ...skipped, explanation: "Even at the break-even search ceiling the project cannot reach NPV = 0." };
    expect(metricExplanationText(skipped, te, "te").explanation).toContain("గణన జరగలేదు");
    expect(metricExplanationText(failed, te, "te").explanation).toContain("గరిష్ఠ ధర వద్ద కూడా NPV సున్నా కాలేదు");
  });

  it("keeps terminal-driven payback and missing repayment years explicit", () => {
    expect(metricExplanationText(metric("payback_month", { payback_month: 60, terminal_driven: 1 }), te, "te").explanation)
      .toContain("నిర్వహణ నుంచి క్రమంగా తిరిగి రాదు");
    expect(metricExplanationText(metric("avg_dscr", { avg_dscr: null }), te, "te").explanation)
      .toContain("DSCR అందుబాటులో లేదు");
    expect(metricExplanationText(metric("npv", { npv: -1000 }), te, "te").explanation)
      .toContain("పెట్టుబడి విలువను తగ్గిస్తుంది");
  });

  it("uses a translated unknown-basis notice while preserving new quantities and source links", () => {
    const entry = metric("future_metric");
    entry.explanation = "New English economic claim at ₹1,23,456/kg; source https://example.test/policy-2026.";
    const text = metricExplanationText(entry, te, "te");
    expect(text.explanation).toContain("అనువాదం ఇంకా అందుబాటులో లేదు");
    expect(text.explanation).toContain("₹1,23,456/కిలో");
    expect(text.explanation).toContain("https://example.test/policy-2026");
    expect(text.explanation).not.toContain("New English economic claim");
  });

  it("translates combined source provenance without changing recorded values or dates", () => {
    const item = evidence("Quantity-weighted structured feed purchase unit price", "transactions/feed_inventory");
    const text = calibrationEvidenceText(item, te, "te");
    expect(text.source).toBe("ఆర్థిక లావాదేవీల పుస్తకం / మేత నిల్వ నమోదులు (transactions/feed_inventory)");
    expect(text.method).toContain("కొనుగోలు పరిమాణాల ఆధారంగా");
    expect(item).toMatchObject({ previous_value: 250, calibrated_value: 600, sample_size: 18, period_start: "2026-01-01" });
  });

  it("retains fractional staffing, actual ledger months and breeder denominators", () => {
    const item = evidence("Total labour expense divided by the 7 month(s) of ledger history, then split across the 0.5 attendant unit(s) implied by 18 adult female(s) (the engine's labour basis, matching one worker per ~50 does with progeny)");
    const text = calibrationEvidenceText(item, te, "te");
    for (const number of ["7", "0.5", "18", "50"]) expect(text.method).toContain(number);
    expect(text.method).toContain("కార్మిక యూనిట్లతో భాగిస్తారు");
    expect(text.method).not.toContain("Total labour expense");
    expect(calibrationEvidenceText(evidence("Vet and medicine spend divided by the 9 month(s) of ledger history, annualized and divided by active head"), te, "te").method)
      .toContain("9 నెలలతో భాగించి, వార్షిక మొత్తంగా");
  });

  it("explains zero paid staffing without inventing a calibrated wage", () => {
    const item = evidence("Labour expense of 35000 was NOT spread onto a per-attendant wage: with 12 adult female(s) the engine's labour basis implies 0 paid attendant units, so the configured wage was left unchanged.");
    const text = calibrationEvidenceText(item, te, "te");
    expect(text.method).toContain("35000");
    expect(text.method).toContain("12");
    expect(text.method).toContain("వేతనాన్ని మార్చలేదు; సున్నాతో భాగించలేదు");
  });

  it("keeps the birth-weight adjustment on the fitted growth curve", () => {
    const method = "Median recorded weight per age-month, isotonic-fitted; unobserved ages interpolated between fitted points and the preset shape rescaled outside them; age zero set from median recorded birth weight";
    const text = calibrationEvidenceText(evidence(method, "weight_records/kid_entries"), te, "te");
    expect(text.method).toContain("సున్నా వయస్సు బరువుకు కూడా");
    expect(text.method).toContain("తగ్గకుండా ఉండే పెరుగుదల వక్రరేఖ");
  });

  it("does not fabricate a basis for a future calibration method", () => {
    const text = calibrationEvidenceText(evidence("Novel opaque English algorithm with 22 measurements"), te, "te");
    expect(text.method).toContain("పద్ధతి అనువాదం ఇంకా లేదు");
    expect(text.method).toContain("22");
    expect(text.method).not.toContain("Novel opaque English");
  });

  it.each([
    ["Animal-history calibration used the 10,000 most recent rows; current herd counts remain exact.", "10,000"],
    ["Cost calibration used the 10,000 most recent ledger rows (ledger history actually reaches back to 2025-11-03).", "2025-11-03"],
    ["4 sale-price observations used an estimated exit weight from a routine weighing within 30 days before sale.", "30"],
    ["3 sale-price observations were excluded: no recorded sale weight or routine weighing within 30 days.", "3"],
    ["Recurring costs were averaged over the 6 month(s) of ledger history that exist, not the 24 month(s) requested.", "24"],
    ["Observed stillbirth rate 80% exceeds the model ceiling of 50%; it was capped at 50%.", "80%"],
    ["5 calibrated input(s) have low confidence; review them before lending or investment decisions.", "5"],
  ])("preserves numeric evidence limits when translating %s", (warning, quantity) => {
    const text = calibrationWarningText(warning, te, "te");
    expect(text).toMatch(/[\u0c00-\u0c7f]/);
    expect(text).toContain(quantity);
    expect(text).not.toContain("simulation.evidence.");
    expect(text).not.toContain("calibration used");
    expect(text).not.toContain("sale-price observations");
  });

  it("renders missing evidence groups in Telugu and retains preset status", () => {
    const text = calibrationWarningText("No sufficient farm evidence for: feed, mortality, reproduction; preset values remain.", te, "te");
    expect(text).toContain("మేత / మరణాలు / సంతానోత్పత్తి");
    expect(text).toContain("ముందస్తు విలువలనే ఉంచారు");
    expect(calibrationWarningText("No active animals were found; herd counts were calibrated to zero.", te, "te"))
      .toContain("ప్రారంభ మంద సంఖ్యను సున్నాగా");
  });
});

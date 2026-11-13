import { expect, it } from "vitest";
import type { CalibrationEvidence, ReportSection } from "@/api/generated/models";
import { translate, type TFn } from "@/lib/i18n";
import { calibrationEvidenceText, calibrationWarningText, narrativeSectionText } from "./simulation-evidence-text";

const te: TFn = (key, vars) => translate("te", key, vars);
it.each(["constructor", "toString", "__proto__", "hasOwnProperty"])("uses the unknown-evidence language boundary for an opaque %s label", (label) => {
  const item: CalibrationEvidence = { path: "future_input", previous_value: 1, calibrated_value: 2,
    sample_size: 3, confidence: "medium", source: label, method: label };
  let result: ReturnType<typeof calibrationEvidenceText> | undefined;
  expect(() => { result = calibrationEvidenceText(item, te, "te"); }).not.toThrow();
  expect(result?.source).toBe(`నమోదైన మూల ఆధారం (${label})`);
  expect(result?.method).toContain("ఈ నమోదైన లెక్కింపు పద్ధతి అనువాదం ఇంకా లేదు.");
  expect(result?.method).not.toContain("function");
  expect(result?.method).not.toContain("[object Object]");
});

it.each(["constructor", "toString", "__proto__", "hasOwnProperty"])("marks an unrecognized %s report title and paragraph incomplete", (label) => {
  const section: ReportSection = { key: label, title: "Unknown report section", paragraphs: [label] };
  let result: ReturnType<typeof narrativeSectionText> | undefined;
  expect(() => { result = narrativeSectionText(section, te, "te"); }).not.toThrow();
  expect(result?.complete).toBe(false);
  expect(result?.title).toBe("నివేదిక విభాగం");
  expect(result?.paragraphs[0]).toContain("ఈ పేరాకు పూర్తి అనువాదం ఇంకా అందుబాటులో లేదు.");
  expect(result?.paragraphs[0]).not.toContain("simulation.narrative.");
});

it.each([
  "Watch out: constructor.",
  "Watch out: a unique IRR is not established (assessment: constructor); use NPV and MIRR.",
  "The assumptions that move NPV the most: constructor (+1% moves NPV by ₹1).",
])("keeps an unsupported economic qualifier visibly incomplete: %s", (value) => {
  const section: ReportSection = { key: "risks", title: "Risk report", paragraphs: [value] };
  let result: ReturnType<typeof narrativeSectionText> | undefined;
  expect(() => { result = narrativeSectionText(section, te, "te"); }).not.toThrow();
  expect(result?.complete).toBe(false);
  expect(result?.paragraphs[0]).toContain("ఈ పేరాకు పూర్తి అనువాదం ఇంకా అందుబాటులో లేదు.");
  expect(result?.paragraphs[0]).not.toContain("simulation.narrative.");
});

it("uses a registered unknown-source label for future missing-evidence groups", () => {
  const text = calibrationWarningText("No sufficient farm evidence for: feed, constructor, __proto__; preset values remain.", te, "te");
  expect(text).toContain("మేత / నమోదైన మూల ఆధారం / నమోదైన మూల ఆధారం");
  expect(text).not.toContain("function");
  expect(text).not.toContain("[object Object]");
});

it("marks an unsupported adverse-event kind incomplete rather than inventing its label", () => {
  const section: ReportSection = { key: "risks", title: "Risk report", paragraphs: [
    "Across 100 correlated Monte Carlo runs (varying prices, feed, fodder yield, operating cost, mortality and reproduction), the NPV averages ₹1 with a 90% range of ₹0 to ₹2. The project loses money in 1% of runs and runs short of operating cash in 2%. Every risk path also draws constructor events, so this distribution sits below the headline (no-disaster) figures — compare runs against each other, not against the deterministic base case.",
  ] };
  const result = narrativeSectionText(section, te, "te");
  expect(result.complete).toBe(false);
  expect(result.paragraphs[0]).toContain("ఈ పేరాకు పూర్తి అనువాదం ఇంకా అందుబాటులో లేదు.");
  expect(result.paragraphs[0]).not.toContain("simulation.narrative.");
});

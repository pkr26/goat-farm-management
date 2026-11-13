import { expect, it } from "vitest";

import { farmVocabulary } from "@/lib/farm-vocabulary";
import { translate, type TFn } from "@/lib/i18n";
import { simulationFieldHelp, simulationSectionHelp } from "@/lib/simulation-field-help";

const en: TFn = (key, vars) => translate("en", key, vars);
const te: TFn = (key, vars) => translate("te", key, vars);
const inheritedNames = ["constructor", "toString", "hasOwnProperty", "__proto__"];

it.each(inheritedNames)("has no section explanation for unknown inherited name %s", (name) => {
  for (const translator of [undefined, en, te]) {
    let result: ReturnType<typeof simulationSectionHelp> | undefined;
    expect(() => { result = simulationSectionHelp(name, farmVocabulary, translator); }).not.toThrow();
    expect(result).toBeNull();
  }
});

it.each(inheritedNames)("has no field explanation for unknown inherited path %s", (path) => {
  for (const translator of [undefined, en, te]) {
    let result: ReturnType<typeof simulationFieldHelp> | undefined;
    expect(() => { result = simulationFieldHelp(path, farmVocabulary, translator); }).not.toThrow();
    expect(result).toBeNull();
  }
});

it.each(inheritedNames)("has no risk explanation for unknown inherited subfield %s", (subfield) => {
  for (const translator of [undefined, en, te]) {
    let result: ReturnType<typeof simulationFieldHelp> | undefined;
    expect(() => { result = simulationFieldHelp(`risk.meat_price.${subfield}`, farmVocabulary, translator); }).not.toThrow();
    expect(result).toBeNull();
  }
});

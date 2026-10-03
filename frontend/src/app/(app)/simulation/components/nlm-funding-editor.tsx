"use client";

import type { FinanceAssumptions } from "@/api/generated/models";
import { Label } from "@/components/ui/label";
import { useT, type MessageKey } from "@/lib/i18n";
import { NumberInput } from "./number-inputs";

export const NLM_EVIDENCE_FIELDS = new Set([
  "nlm_unit_females", "nlm_unit_males", "nlm_eligible_capital_cost",
  "nlm_approved_subsidy_amount", "nlm_subsidy_receipts",
]);

/** Mirrors the conservative policy evidence boundary; the server validates again. */
export function validNlmFunding(finance: FinanceAssumptions, does = 0, bucks = 0): boolean {
  const f = finance.nlm_unit_females;
  const m = finance.nlm_unit_males;
  if ((f == null) !== (m == null)) return false;
  const approved = finance.nlm_approved_subsidy_amount;
  const receipts = finance.nlm_subsidy_receipts ?? [];
  if (approved != null) {
    if (!finance.nlm_subsidy || finance.nlm_eligible_capital_cost == null) return false;
    if (approved > finance.nlm_eligible_capital_cost * 0.5 + 0.005) return false;
    if (approved > 0) {
      const females = f ?? does, males = m ?? bucks;
      const band = females / 100;
      if (!Number.isInteger(band) || band < 1 || band > 5 || males !== band * 5) return false;
      if (approved > band * 1_000_000 + 0.005) return false;
    }
  }
  if (receipts.length && !(approved != null && approved > 0)) return false;
  if (receipts.length > 2) return false;
  return receipts.every((receipt, index) =>
    Number.isInteger(receipt.month) && receipt.month >= 1 && receipt.month <= 240 &&
    Math.abs(receipt.amount - (approved ?? 0) / 2) <= 0.005 &&
    (index === 0 || receipt.month >= receipts[index - 1].month));
}

export function NlmFundingEditor({ value, prefix = "sim", onChange, onValidityChange }: {
  value: FinanceAssumptions;
  prefix?: string;
  onChange: (patch: Partial<FinanceAssumptions>) => void;
  onValidityChange: (key: string, valid: boolean) => void;
}) {
  const t = useT();
  const fields: [keyof FinanceAssumptions, MessageKey, boolean][] = [
    ["nlm_unit_females", "simulation.nlm.unitFemales", true],
    ["nlm_unit_males", "simulation.nlm.unitMales", true],
    ["nlm_eligible_capital_cost", "simulation.nlm.eligibleCost", false],
    ["nlm_approved_subsidy_amount", "simulation.nlm.approvedAmount", false],
  ];
  const approved = value.nlm_approved_subsidy_amount;
  const receipts = value.nlm_subsidy_receipts ?? [];
  return <fieldset className="space-y-3 rounded-lg border p-3 sm:col-span-2 lg:col-span-3">
    <legend className="px-1 text-sm font-medium">{t("simulation.nlm.title")}</legend>
    <p className="text-xs text-muted-foreground">{t("simulation.nlm.help")}</p>
    <div className="grid gap-3 sm:grid-cols-2">
      {fields.map(([key, label, integer]) => {
        const id = `${prefix}-finance-${key}`;
        return <div key={key} className="space-y-1.5">
          <Label htmlFor={id}>{t(label)}</Label>
          <NumberInput id={id} nullable value={value[key] as number | null ?? null}
            integer={integer} min={0} max={integer ? 100_000 : 1_000_000_000}
            onValidityChange={(valid) => onValidityChange(id, valid)}
            onCommit={(number) => onChange({ [key]: number,
              ...(key === "nlm_approved_subsidy_amount" ? { nlm_subsidy_receipts: [] } : {}) })} />
        </div>;
      })}
      {[0, 1].map((index) => {
        const id = `${prefix}-nlm-receipt-${index}`;
        return <div key={`${id}:${approved}`} className="space-y-1.5">
          <Label htmlFor={id}>{t("simulation.nlm.receiptMonth", { index: index + 1 })}</Label>
          <NumberInput id={id} nullable integer min={1} max={240}
            disabled={!(approved != null && approved > 0) || (index === 1 && !receipts[0])}
            value={receipts[index]?.month ?? null}
            onValidityChange={(valid) => onValidityChange(id, valid)}
            onCommit={(month) => {
              const next = receipts.slice(0, index);
              if (month != null && approved != null && approved > 0) {
                next.push({ month, amount: approved / 2 });
                if (receipts[index + 1]) next.push(receipts[index + 1]);
              }
              onChange({ nlm_subsidy_receipts: next });
            }} />
        </div>;
      })}
    </div>
    <p className="text-xs text-muted-foreground">{t("simulation.nlm.receiptHelp")}</p>
    <a className="text-xs underline" href="https://dahd.gov.in/sites/default/files/2026-04/NLMGuidelinesJan2025.pdf"
      target="_blank" rel="noreferrer">{t("simulation.nlm.policy")}</a>
  </fieldset>;
}

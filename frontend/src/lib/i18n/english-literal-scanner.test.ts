/**
 * Fixture tests for the English-literal scanner (AUDIT_REPORT_2026-09-28,
 * H7 — the 2026-09-28 expansion that taught the gate to see JSX expression
 * containers, camelCase copy props, default parameters, toasts and zod
 * messages). Each case pins both what the scanner MUST catch (the
 * categories the audit showed escaping) and what it must NOT flag (codes,
 * CSS, identifiers, catalog keys — false positives pollute the baseline
 * permanently).
 */

import { describe, expect, it } from "vitest";

import { scanTextLiterals } from "../../../scripts/scan-english-literals.mjs";

function scan(source: string, jsx = true): string[] {
  return [...scanTextLiterals(source, { jsx })].sort();
}

describe("scanTextLiterals — newly visible categories", () => {
  it("catches string literals in JSX expression containers", () => {
    const found = scan(
      `<button>{isSubmitting ? "Saving…" : "Add transaction"}</button>
       {ready && "All records checked."}
       <span>{"Confirm clearance"}</span>`,
    );
    expect(found).toContain("Saving…");
    expect(found).toContain("Add transaction");
    expect(found).toContain("All records checked.");
    expect(found).toContain("Confirm clearance");
  });

  it("catches expanded attribute names and camelCase copy props", () => {
    const found = scan(
      `<RemotePicker
         emptyMessage="No active animals match this search."
         helperText="Use the herd tag or the exact #id."
         dialogTitle="Pick an animal"
         dialogDescription="Find a targetable purchase batch."
         searchLabel="Search health animals"
         searchPlaceholder="Enter batch ID…"
         noAccessMessage="You don't have access to this page."
       />`,
    );
    expect(found).toContain("No active animals match this search.");
    expect(found).toContain("Use the herd tag or the exact #id.");
    expect(found).toContain("Pick an animal");
    expect(found).toContain("Find a targetable purchase batch.");
    expect(found).toContain("Search health animals");
    expect(found).toContain("Enter batch ID…");
    expect(found).toContain("You don't have access to this page.");
  });

  it("catches default-parameter string literals", () => {
    const found = scan(
      `function Picker({ placeholder = "Pick a purchase batch", emptyMessage = "No matching options." }) {}`,
    );
    expect(found).toContain("Pick a purchase batch");
    expect(found).toContain("No matching options.");
  });

  it("catches template-literal copy attributes and containers (2026-09-29)", () => {
    const found = scan(
      `<Link aria-label={\`Switch farm — current: \${farmName}\`} />
       <span>{\`\${count} records could not be sent.\`}</span>
       <div title={\`Details for \${name}\`} />`,
    );
    // Interpolation is kept for entry stability: the surviving English prose
    // is flagged, and a template may open with an interpolation (mid-sentence
    // lowercase prose still counts).
    expect(found).toContain("Switch farm — current: ${farmName}");
    expect(found).toContain("${count} records could not be sent.");
    expect(found).toContain("Details for ${name}");
    // Pure interpolation with no prose remains invisible.
    expect(found).not.toContain("${name}");
  });

  it("catches toast literals, including templates with interpolation", () => {
    const found = scan(
      `toast.success("Purchase batch created.");
       toast.error("Fix the targets before saving the plan.");
       toast.info(\`Starting stock set to your current herd (\${snap.total_head} head).\`);
       toast.warning(\`Saved plan “\${name}”.\`);`,
    );
    expect(found).toContain("Purchase batch created.");
    expect(found).toContain("Fix the targets before saving the plan.");
    expect(found).toContain(
      "Starting stock set to your current herd (${snap.total_head} head).",
    );
    expect(found).toContain("Saved plan “${name}”.");
  });

  it("catches zod message literals in every shape the codebase uses", () => {
    const found = scan(
      `const schema = z.object({
         date: z.string().min(1, "Date is required"),
         notes: z.string().max(4_000, "Notes cannot exceed 4000 characters").optional(),
         email: z.string().email("Enter a valid email address"),
         premium: z.number().nonnegative("Premium cannot be negative")
           .max(MAX_AMOUNT, \`Premium cannot exceed \${formatMoney(MAX_AMOUNT)}\`)
           .refine(isPersistable, "Amount must be ₹0 or at least ₹0.005"),
       }).refine((v) => v.a === v.b, {
         path: ["confirm_password"],
         message: "Passwords do not match",
       });`,
    );
    expect(found).toContain("Date is required");
    expect(found).toContain("Notes cannot exceed 4000 characters");
    expect(found).toContain("Enter a valid email address");
    expect(found).toContain("Premium cannot be negative");
    expect(found).toContain("Premium cannot exceed ${formatMoney(MAX_AMOUNT)}");
    expect(found).toContain("Amount must be ₹0 or at least ₹0.005");
    expect(found).toContain("Passwords do not match");
  });

  it("catches exported message constants", () => {
    const found = scan(
      `export const MIN_PERSISTED_MONEY_MESSAGE = "Amount must be ₹0 or at least ₹0.005";`,
      false,
    );
    expect(found).toContain("Amount must be ₹0 or at least ₹0.005");
  });
});

describe("scanTextLiterals — false-positive classes stay out", () => {
  it("ignores catalog lookups, import paths, storage keys and testids", () => {
    const found = scan(
      `import { useT } from "@/lib/i18n";
       const label = t("animals.detail.title");
       localStorage.getItem("herdly.language");
       <div data-testid="worker-queue-depth" />`,
    );
    expect(found).toEqual([]);
  });

  it("ignores CSS-ish strings, including className ternaries", () => {
    const found = scan(
      `<div className={busy ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950" : "bg-muted/40"}>
         <path d="M12 4v16m8-8H4" strokeLinecap="round" />
       </div>`,
    );
    expect(found).toEqual([]);
  });

  it("ignores identifiers, codes, enum operands and format strings", () => {
    const found = scan(
      `const defaults = { timeZone: "Asia/Kolkata", method: "POST", currency: "INR" };
       z.string().refine((tz) => tz !== "Factory" && tz !== "localtime", message);
       value.toLocaleString("en-IN", { dateStyle: "medium" });
       const DATE_FMT = "YYYY-MM-DD";`,
    );
    expect(found).toEqual([]);
  });

  it("ignores symbols, numbers, single characters and interpolation-only fragments", () => {
    const found = scan(
      `<span>{"—"}</span>
       <td>{"3"}</td>
       {count && \`\${count}\`}
       toast.error(message);
       toast.error(errorMessage(err, t("planner.dprFailed")));`,
    );
    expect(found).toEqual([]);
  });

  it("ignores zod path arrays and non-prose args", () => {
    const found = scan(
      `z.object({ a: z.string() }).refine((v) => v.a.length > 0, {
         path: ["confirm_password"],
         message: t("account.errors.mismatch"),
       });
       const capped = Math.min(Math.max(value, 0), 100);`,
    );
    expect(found).toEqual([]);
  });

  it("skips the JSX categories entirely for plain .ts modules", () => {
    const found = scan(
      `const tone = done ? "Done" : "Pending";
       export const LABEL = { empty: "No data" };
       toast.success("Role saved.");`,
      false,
    );
    expect(found).toEqual(["Role saved."]);
  });
});

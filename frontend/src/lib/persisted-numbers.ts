/** Smallest non-zero values that survive backend storage normalization. */
export const MIN_PERSISTED_KG = 0.0005;
export const MIN_PERSISTED_MONEY = 0.005;

export const MIN_PERSISTED_KG_MESSAGE = "Quantity must be at least 0.0005 kg";
export const MIN_PERSISTED_MONEY_MESSAGE = "Amount must be ₹0 or at least ₹0.005";

/** Render stock-ledger quantities without hiding gram-scale values.  Keep one
 * decimal for ordinary whole-kilogram readings while preserving all three
 * decimals the API stores when they are significant. */
export function formatPersistedKg(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 3,
  });
}

/** Zero is meaningful for optional/non-negative money fields; a smaller
 * non-zero value would round back to zero and is therefore rejected. */
export function isPersistableNonnegativeMoney(value: number): boolean {
  return Number.isFinite(value) && (value === 0 || value >= MIN_PERSISTED_MONEY);
}

/** Same rule for optional weights stored at 3-decimal precision (kg). */
export function isPersistableNonnegativeWeight(value: number): boolean {
  return Number.isFinite(value) && (value === 0 || value >= MIN_PERSISTED_KG);
}

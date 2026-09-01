/** Single source of truth for the product name. Every user-facing brand
 * string — wordmark, document titles, aria-labels — flows from here, so a
 * future rename is a one-line change. Internal identifiers (storage keys,
 * env vars, DB name) deliberately do NOT use this: they stay stable so
 * sessions and data survive rebrands. */
export const APP_NAME = "Herdly";

/** Single source of truth for the product name. Every user-facing brand
 * string — wordmark, document titles, aria-labels — flows from here, so a
 * future rename is a one-line change. Internal identifiers (storage keys,
 * env vars, DB name) deliberately do NOT use this: they stay stable so
 * sessions and data survive rebrands.
 *
 * Storage-key policy: the keys predate the Herdly name and are deliberately
 * split across TWO frozen namespaces — legacy `goatfarm.*` keys
 * (goatfarm.farmId, goatfarm:offlineQueue:v1, goatfarm:idempotency:v1) and
 * newer `herdly.*`-era keys (herdly.language, herdly.tabletFarm). Both are
 * stable forever: renaming a key would strand the data every installed
 * tablet and browser profile already carries, so new keys pick either
 * namespace and never migrate old ones. */
export const APP_NAME = "Herdly";

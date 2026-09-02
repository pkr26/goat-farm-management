/**
 * Mirrors of backend sanity caps used by form validation.
 *
 * Single module so the duplicated literals cannot drift silently; the
 * parity test asserts these against shared/openapi.json (the generated
 * contract), which CI already keeps in lockstep with the backend.
 */

// models.MAX_ANIMAL_TAG_LENGTH (animals tag, kid tags).
export const MAX_ANIMAL_TAG_LENGTH = 50;
// models.MAX_TASK_TITLE_LENGTH.
export const MAX_TASK_TITLE_LENGTH = 200;
// models.MAX_RECUR_DAYS (task recurrence).
export const MAX_RECUR_DAYS = 3650;
// models.MAX_AGE_MONTHS (purchase average age).
export const MAX_AGE_MONTHS = 240;
// models.MAX_BATCH_COUNT (animals per purchase batch).
export const MAX_BATCH_COUNT = 1000;
// models.MAX_WITHDRAWAL_DAYS (health event withdrawal window).
export const MAX_WITHDRAWAL_DAYS = 730;
// schemas.common.MAX_FREE_TEXT_LENGTH (notes fields).
export const MAX_FREE_TEXT_LENGTH = 4000;

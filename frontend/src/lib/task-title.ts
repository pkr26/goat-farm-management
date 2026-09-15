/**
 * Localized rendering for auto-generated duty titles.
 *
 * Task payloads carry `title_key` + `title_args` alongside the legacy English
 * `title` (the backend emits them for all 40 generated duty types — see
 * audit_reports/2026-09-14/task_title_keys.md — on TaskOut and
 * QuarantineScheduleTaskOut; manual duties have title_key null). When the key
 * exists in the catalog, the title renders in the worker's language via
 * `taskGen.<key>` with argument interpolation; anything else — a missing
 * field, an unknown key, a manual duty — falls back to `task.title`
 * byte-for-byte, so older payloads and new duty types never render blank.
 *
 * Arg conventions from the contract:
 * - `*_date` values are ISO YYYY-MM-DD and render through the locale-aware
 *   formatDate (Telugu month names in te sessions).
 * - `{month}` arrives as an English month name (cadence rounds) and maps to
 *   the localized taskGen.month.* keys.
 * - `kidding_watch` carries `days_before`; 0 is the due-day labor watch with
 *   the dystocia escalation rule, rendered by the kidding_watch_due variant.
 */

import { formatDate } from "@/lib/format";
import en, { type MessageKey } from "@/lib/i18n/en";
import { translate, type Language } from "@/lib/i18n";

/** The wire shape the backend contract adds to TaskOut. Optional throughout:
 * the fields are absent until the backend ships them. */
export interface TaskTitleSource {
  title: string;
  title_key?: string | null;
  title_args?: Record<string, unknown> | null;
}

const TASKGEN_PREFIX = "taskGen.";
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

const ENGLISH_MONTHS: Record<string, number> = {
  january: 1,
  february: 2,
  march: 3,
  april: 4,
  may: 5,
  june: 6,
  july: 7,
  august: 8,
  september: 9,
  october: 10,
  november: 11,
  december: 12,
};

/** Month args (English name per the contract, numeric defensively) resolve
 * through the localized taskGen.month.* keys. */
function localizedArg(
  name: string,
  value: unknown,
  language: Language,
): string | number {
  if (name === "month") {
    const month =
      typeof value === "string" && /^\d+$/.test(value)
        ? Number(value)
        : typeof value === "string"
          ? ENGLISH_MONTHS[value.toLowerCase()]
          : value;
    if (typeof month === "number" && month >= 1 && month <= 12) {
      return translate(language, `taskGen.month.${month}` as MessageKey);
    }
  }
  if ((name === "date" || name.endsWith("_date")) && typeof value === "string" && ISO_DATE.test(value)) {
    return formatDate(value, language);
  }
  return typeof value === "number" && Number.isFinite(value) ? value : String(value);
}

export function resolveTaskTitle(task: TaskTitleSource, language: Language): string {
  let key = task.title_key ? `${TASKGEN_PREFIX}${task.title_key}` : null;
  // Unknown keys (a backend ahead of this catalog) render the payload's own
  // title — the fallback chain is te → en → never the raw key.
  if (!key || !(key in en)) return task.title;
  if (task.title_key === "kidding_watch" && Number(task.title_args?.days_before) === 0) {
    key = `${TASKGEN_PREFIX}kidding_watch_due`;
  }
  const args: Record<string, string | number> = {};
  for (const [name, value] of Object.entries(task.title_args ?? {})) {
    if (value === null || value === undefined) continue;
    args[name] = localizedArg(name, value, language);
  }
  return translate(language, key as MessageKey, args);
}

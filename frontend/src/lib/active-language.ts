/**
 * Module-level mirror of the active UI language for non-React helpers
 * (format.ts date rendering, enum-labels defaults). The LanguageProvider
 * syncs it on every language change — the same external-store pattern
 * format.ts already uses for the active farm timezone. Components that need
 * reactivity must still consume the context (useLanguage / useT); this store
 * only gives pure helpers the current default.
 */

import type { Language } from "@/lib/i18n";

let activeLanguage: Language = "en";

export function getActiveLanguage(): Language {
  return activeLanguage;
}

export function setActiveLanguage(language: Language): void {
  activeLanguage = language;
}

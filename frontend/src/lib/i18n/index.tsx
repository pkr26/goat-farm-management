"use client";

/**
 * Minimal dependency-free i18n layer (no next-intl: the audit's constraint is
 * a low-bandwidth bundle). One React context holds the language choice in
 * localStorage under a namespaced key; `useT()` resolves keys against the
 * Telugu catalog first and the English catalog as the universal fallback.
 *
 * Consumers that render without a provider (unit tests, storybook-ish
 * islands) get a default context that behaves exactly like English with a
 * no-op setter, so wiring a page to `useT()` can never crash a test that
 * predates the provider.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import en, { type MessageKey } from "./en";
import te from "./te";

export const LANGUAGES = ["en", "te"] as const;
export type Language = (typeof LANGUAGES)[number];
/** Namespaced so a shared origin / embedded webview never collides. */
export const LANGUAGE_STORAGE_KEY = "herdly.language";

/** Interpolates `{name}` tokens; unknown tokens are left verbatim so a
 * missing variable is visible in review instead of silently swallowed. */
export function interpolate(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (token, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : token,
  );
}

/** Pure resolver: Telugu first, English fallback, key itself as last resort
 * (a typo'd key should fail loudly in dev, not render as undefined). */
export function translate(
  language: Language,
  key: MessageKey,
  vars?: Record<string, string | number>,
): string {
  const template = language === "te" ? (te[key] ?? en[key]) : en[key];
  return interpolate(template ?? key, vars);
}

export type TFn = (key: MessageKey, vars?: Record<string, string | number>) => string;

interface LanguageContextValue {
  language: Language;
  setLanguage: (language: Language) => void;
  t: TFn;
}

const defaultContextValue: LanguageContextValue = {
  language: "en",
  setLanguage: () => {},
  t: (key, vars) => translate("en", key, vars),
};

const LanguageContext = createContext<LanguageContextValue>(defaultContextValue);

function readStoredLanguage(): Language {
  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return stored === "te" ? "te" : "en";
  } catch {
    // Private-mode webviews can throw on storage access; English is the
    // safe default and the toggle still works for the live session.
    return "en";
  }
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  // Starts as English on both server and first client render (no hydration
  // mismatch); the stored choice is adopted in the effect below.
  const [language, setLanguageState] = useState<Language>("en");

  // The stored choice is an external store that only exists client-side;
  // adopting it after mount (not during render) keeps SSR output English and
  // the first client render hydration-safe.
  useEffect(() => {
    const stored = readStoredLanguage();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (stored !== "en") setLanguageState(stored);
  }, []);

  // Keep <html lang> truthful for screen readers and Telugu keyboard hints.
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const setLanguage = useCallback((next: Language) => {
    setLanguageState(next);
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, next);
    } catch {
      // Storage unavailable (private mode): keep the in-memory switch.
    }
  }, []);

  const value = useMemo<LanguageContextValue>(
    () => ({ language, setLanguage, t: (key, vars) => translate(language, key, vars) }),
    [language, setLanguage],
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageContextValue {
  return useContext(LanguageContext);
}

/** `const t = useT()` — resolves message keys for the active language. */
export function useT(): TFn {
  return useLanguage().t;
}

export type { MessageKey };

"use client";

/**
 * A lightweight language context with a lazily loaded Telugu catalog and an English
 * fallback. Persist the choice in localStorage and a server-readable cookie.
 * Consumers outside the provider use English with a no-op setter.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { getActiveLanguage, setActiveLanguage } from "@/lib/active-language";
import { safeStorage } from "@/lib/safe-storage";

import en, { type MessageKey } from "./en";
import {
  LANGUAGES,
  LANGUAGE_COOKIE_KEY,
  LANGUAGE_STORAGE_KEY,
  type Language,
} from "./config";

type MessageCatalog = Partial<Record<MessageKey, string>>;
export { LANGUAGES, LANGUAGE_COOKIE_KEY, LANGUAGE_STORAGE_KEY };
export type { Language };

let teluguCatalog: MessageCatalog | undefined;
let teluguCatalogPromise: Promise<MessageCatalog> | undefined;

/** Install a catalog that has already been fetched. Exported so the test
 * harness can keep pure translation tests synchronous without putting the
 * Telugu table back into production's shared route graph. */
export function installLanguageCatalog(language: Language, catalog: MessageCatalog): void {
  if (language === "te") teluguCatalog = catalog;
}

export function languageCatalogIsLoaded(language: Language): boolean {
  return language === "en" || teluguCatalog !== undefined;
}

/** The explicit import expression is a bundler split point. A failed offline
 * first-load remains retryable; once fetched, the service worker's existing
 * immutable-chunk cache keeps the locale available offline. */
export async function loadLanguageCatalog(language: Language): Promise<void> {
  if (language === "en" || teluguCatalog) return;
  teluguCatalogPromise ??= import("./te")
    .then((module) => {
      teluguCatalog = module.default;
      return module.default;
    })
    .catch((error: unknown) => {
      teluguCatalogPromise = undefined;
      throw error;
    });
  await teluguCatalogPromise;
}

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
  const template = language === "te" ? (teluguCatalog?.[key] ?? en[key]) : en[key];
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

/** Reads the persisted language without React — for the route-state
 * boundaries (error/404/loading) that must render even when the provider
 * tree has crashed. */
function readStoredLanguageChoice(): Language | null {
  try {
    const stored = safeStorage("local")?.getItem(LANGUAGE_STORAGE_KEY);
    return stored === "en" || stored === "te" ? stored : null;
  } catch {
    return null;
  }
}

export function readStoredLanguage(): Language {
  // Private-mode webviews can throw on storage access; English is the safe
  // default and the toggle still works for the live session.
  return readStoredLanguageChoice() ?? "en";
}

function persistLanguage(language: Language): void {
  try {
    safeStorage("local")?.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    // Storage unavailable (private mode): keep the in-memory switch.
  }
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${LANGUAGE_COOKIE_KEY}=${language}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
}

export function LanguageProvider({
  children,
  initialLanguage,
}: {
  children: ReactNode;
  /** `null` means the server found no valid cookie and the client must
   * reconcile legacy localStorage before exposing localized UI. Omitted is
   * retained for standalone/test mounts and starts in English immediately. */
  initialLanguage?: Language | null;
}) {
  const initial = initialLanguage ?? "en";
  const [language, setLanguageState] = useState<Language>(initial);
  const [catalogReady, setCatalogReady] = useState(
    () => initialLanguage !== null && languageCatalogIsLoaded(initial) && getActiveLanguage() === initial,
  );
  const [failedLanguage, setFailedLanguage] = useState<Language | null>(null);
  const switchVersion = useRef(0);

  // The root provider owns the helper default. Update it in the same
  // committed transition BEFORE notifying context consumers, never during
  // render or in an effect after those consumers have formatted their data.
  const commitLanguage = useCallback((next: Language, persist: boolean) => {
    setActiveLanguage(next);
    document.documentElement.lang = next;
    setLanguageState(next);
    setCatalogReady(true);
    setFailedLanguage(null);
    if (persist) persistLanguage(next);
  }, []);

  const requestLanguage = useCallback((next: Language, persist: boolean) => {
    const version = ++switchVersion.current;
    const commit = () => {
      if (version === switchVersion.current) commitLanguage(next, persist);
    };
    if (languageCatalogIsLoaded(next)) {
      commit();
      return;
    }
    void loadLanguageCatalog(next).then(commit).catch(() => {
      if (version === switchVersion.current) setFailedLanguage(next);
    });
  }, [commitLanguage]);

  useEffect(() => {
    // Resolve the worker's first preference before persisting any default.
    // A stored preference or valid server cookie always wins.
    const workerRoute = window.location.pathname === "/worker" || window.location.pathname.startsWith("/worker/");
    const desired = readStoredLanguageChoice() ?? initialLanguage ?? (workerRoute ? "te" : "en");
    if (initialLanguage === null) {
      // Keep cookie-less hydration neutral until legacy storage is reconciled.
      let active = true;
      void Promise.resolve().then(() => { if (active) requestLanguage(desired, true); });
      return () => { active = false; switchVersion.current += 1; };
    }
    requestLanguage(desired, false);
    return () => { switchVersion.current += 1; };
  }, [initialLanguage, requestLanguage]);

  useEffect(() => {
    if (!failedLanguage) return;
    const retry = () => requestLanguage(failedLanguage, true);
    window.addEventListener("online", retry);
    return () => window.removeEventListener("online", retry);
  }, [failedLanguage, requestLanguage]);

  const setLanguage = useCallback((next: Language) => {
    requestLanguage(next, true);
  }, [requestLanguage]);

  const value = useMemo<LanguageContextValue>(
    () => ({ language, setLanguage, t: (key, vars) => translate(language, key, vars) }),
    [language, setLanguage],
  );

  const recovery = failedLanguage && <div role="alert" className="space-y-3 border-b bg-card p-4">
    {/* This small bilingual recovery surface must not depend on the failed
        catalog; otherwise an offline locale failure hides its own remedy. */}
    <p><span lang="en">{en["language.recovery.en"]}</span>{" "}
      <span lang="te">{en["language.recovery.te"]}</span></p>
    <div className="flex flex-wrap gap-3">
      <button type="button" className="min-h-11 rounded-md border px-4" onClick={() => requestLanguage(failedLanguage, true)}>
        {en["language.recovery.retry"]}
      </button>
      <button type="button" className="min-h-11 rounded-md border px-4" onClick={() => setLanguage("en")}>EN</button>
    </div>
  </div>;
  if (!catalogReady) {
    return <div className="min-h-screen bg-background" aria-busy={!failedLanguage}>{recovery}</div>;
  }
  return <LanguageContext.Provider value={value}>{recovery}{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageContextValue {
  return useContext(LanguageContext);
}

/** `const t = useT()` — resolves message keys for the active language. */
export function useT(): TFn {
  return useLanguage().t;
}

export type { MessageKey };

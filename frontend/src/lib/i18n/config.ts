export const LANGUAGES = ["en", "te"] as const;
export type Language = (typeof LANGUAGES)[number];

/** Shared between localStorage and the server-readable locale cookie. */
export const LANGUAGE_STORAGE_KEY = "herdly.language";
export const LANGUAGE_COOKIE_KEY = "herdly.language";

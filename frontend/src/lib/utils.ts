import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Defense-in-depth for API-supplied navigation URLs (e.g. task.action_url):
 *  only same-origin, absolute paths are allowed. `javascript:`,
 *  `data:`, cross-origin `//evil.com/...`, and anything not starting with
 *  `/` returns null so callers can render a plain label instead of a link.
 *  Backend also validates, but any XSS-style smuggle attempts must fail here. */
export function safeAppPath(raw: string | null | undefined): string | null {
  if (!raw || typeof raw !== "string") return null
  // Protocol-relative URLs (//host/path) and any scheme (http:, javascript:)
  // both fail the "starts with a single slash" test.
  if (!raw.startsWith("/") || raw.startsWith("//")) return null
  // Browsers treat backslashes as path separators while parsing special
  // schemes.  Consequently `/\\evil.example/path` is not a local path at
  // all: it canonicalizes to `https://evil.example/path`.  Control characters
  // can be stripped during the same parse (`/\n/evil.example` has the same
  // problem), so reject every spelling that changes under URL
  // canonicalization rather than returning the unchecked input to a Link.
  if (/[\\\u0000-\u001F\u007F]/.test(raw)) return null
  const validationOrigin = "https://goatfarm.invalid"
  const rawPathname = raw.split(/[?#]/, 1)[0]
  // App routes are ASCII literals. Encoded pathname bytes can conceal dot
  // segments or separators from permission checks performed by callers.
  if (rawPathname.includes("%")) return null
  try {
    const parsed = new URL(raw, validationOrigin)
    if (parsed.origin !== validationOrigin || parsed.pathname !== rawPathname) return null
  } catch {
    return null
  }
  return raw
}

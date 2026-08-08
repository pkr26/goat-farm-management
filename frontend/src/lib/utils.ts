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
  return raw
}

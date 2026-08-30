/**
 * Orval custom instance: routes every generated call through the app's
 * apiFetch (bearer token, X-Farm-Id, 401→refresh retry).
 *
 * Orval's react-query client types every response as {data, status, headers}
 * and pages unwrap via `query.data?.status === 200 ? query.data.data : …`,
 * so apiFetchEnvelope repackages the parsed body with the response's REAL
 * status (201/204 included) and headers.
 */

import { ApiError, apiFetchEnvelope } from "@/lib/api-client";

/**
 * Orval's generated getUrl serializes a `null` param as the literal string
 * "null" (`?bucket=null`), which the backend treats as a real (never-matching)
 * filter value. Orval 8 exposes no paramsSerializer hook, so the mutator
 * strips literal "null" values here instead — everything except `q`, the one
 * free-text search param where "null" is a legitimate operator query (M-7).
 */
const FREE_TEXT_QUERY_PARAMS = new Set(["q"]);

function stripNullQueryValues(url: string): string {
  const queryStart = url.indexOf("?");
  if (queryStart === -1) return url;
  const params = new URLSearchParams(url.slice(queryStart + 1));
  let changed = false;
  for (const [key, value] of [...params.entries()]) {
    if (value === "null" && !FREE_TEXT_QUERY_PARAMS.has(key)) {
      params.delete(key);
      changed = true;
    }
  }
  return changed ? `${url.slice(0, queryStart)}?${params.toString()}` : url;
}

export const customInstance = async <T>(
  url: string,
  options?: RequestInit,
): Promise<T> => {
  return (await apiFetchEnvelope(stripNullQueryValues(url), options)) as T;
};

/** apiResponse maps every non-2xx body (including FastAPI validation errors)
 * to ApiError before a generated function resolves. Keep Orval's hook error
 * type aligned with that runtime boundary; the schema generic describes the
 * wire body, not the exception callers actually receive. The zero-key Record
 * consumes Orval's required generic without adding anything to ApiError. */
export type ErrorType<WireError> = ApiError & Record<never, WireError>;

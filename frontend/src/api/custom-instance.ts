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

export const customInstance = async <T>(
  url: string,
  options?: RequestInit,
): Promise<T> => {
  return (await apiFetchEnvelope(url, options)) as T;
};

/** apiResponse maps every non-2xx body (including FastAPI validation errors)
 * to ApiError before a generated function resolves. Keep Orval's hook error
 * type aligned with that runtime boundary; the schema generic describes the
 * wire body, not the exception callers actually receive. The zero-key Record
 * consumes Orval's required generic without adding anything to ApiError. */
export type ErrorType<WireError> = ApiError & Record<never, WireError>;

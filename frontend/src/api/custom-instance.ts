/**
 * Orval custom instance: routes every generated call through the app's
 * apiFetch (bearer token, X-Farm-Id, 401→refresh retry).
 *
 * Orval's react-query client types every response as {data, status, headers}
 * and pages unwrap via `query.data?.status === 200 ? query.data.data : …`,
 * so apiFetchEnvelope repackages the parsed body with the response's REAL
 * status (201/204 included) and headers.
 */

import { apiFetchEnvelope } from "@/lib/api-client";

export const customInstance = async <T>(
  url: string,
  options?: RequestInit,
): Promise<T> => {
  return (await apiFetchEnvelope(url, options)) as T;
};

export type ErrorType<Error> = Error;

/**
 * Orval custom instance: routes every generated call through the app's
 * apiFetch (bearer token, X-Farm-Id, 401→refresh retry).
 *
 * Orval's react-query client types every response as {data, status, headers}
 * and pages unwrap via `query.data?.status === 200 ? query.data.data : …`,
 * so the raw body from apiFetch is wrapped into that envelope here.
 */

import { apiFetch } from "@/lib/api-client";

export const customInstance = async <T>(
  url: string,
  options?: RequestInit,
): Promise<T> => {
  const data = await apiFetch<unknown>(url, options);
  return { data, status: 200, headers: new Headers() } as T;
};

export type ErrorType<Error> = Error;

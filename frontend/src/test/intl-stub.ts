/**
 * Deterministic "Intl rejects this timezone" stub.
 *
 * The runtime's tzdata drifts by Node/ICU version: "Factory" (accepted by
 * Python's zoneinfo and PostgreSQL) throws RangeError on some Node builds
 * but is accepted as UTC by Node 24 full-ICU, so tests that rely on a real
 * rejection flip red on newer runtimes. Stub the constructor to reject the
 * given zone exactly as an unaware ICU would; every other zone goes to the
 * real implementation. Returns the spy — restore with `mockRestore()`.
 */

import { vi } from "vitest";

export function stubIntlRejectsTimeZone(zone: string) {
  const RealDateTimeFormat = Intl.DateTimeFormat;
  return vi.spyOn(Intl, "DateTimeFormat").mockImplementation(
    // A function expression (not an arrow) so `new Intl.DateTimeFormat(...)`
    // stays constructible; the returned formatter object overrides `this`.
    function (
      locales?: Intl.LocalesArgument,
      options?: Intl.DateTimeFormatOptions,
    ) {
      if (options?.timeZone === zone) {
        throw new RangeError(`Invalid time zone specified: ${zone}`);
      }
      return new RealDateTimeFormat(locales, options);
    } as typeof Intl.DateTimeFormat,
  );
}

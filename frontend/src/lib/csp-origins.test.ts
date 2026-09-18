import { describe, expect, it } from "vitest";

import { parseCspExtraOrigins } from "@/lib/csp-origins";

describe("parseCspExtraOrigins", () => {
  it("returns an empty list for unset/blank input (CSP unchanged)", () => {
    expect(parseCspExtraOrigins(undefined)).toEqual([]);
    expect(parseCspExtraOrigins("")).toEqual([]);
    expect(parseCspExtraOrigins("  ")).toEqual([]);
  });

  it("accepts absolute http(s) origins, trimmed and deduplicated", () => {
    expect(
      parseCspExtraOrigins(
        " https://bucket.s3.ap-south-1.amazonaws.com,https://bucket.s3.ap-south-1.amazonaws.com , http://minio.local:9000 ",
      ),
    ).toEqual([
      "https://bucket.s3.ap-south-1.amazonaws.com",
      "http://minio.local:9000",
    ]);
  });

  it("keeps only the origin, dropping any path", () => {
    expect(parseCspExtraOrigins("https://cdn.example.com/raw/farm")).toEqual([
      "https://cdn.example.com",
    ]);
  });

  it.each([
    ["not a url", "relative garbage"],
    ["ftp://minio.local:9000", "non-http(s) scheme"],
    ["javascript:alert(1)", "script scheme smuggle"],
  ])("rejects %s (%s) fail-closed", (raw) => {
    expect(() => parseCspExtraOrigins(raw as string)).toThrow(/CSP extra origins/);
  });
});

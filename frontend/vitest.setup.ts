import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./src/test/msw-server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));

afterEach(() => {
  cleanup();
  server.resetHandlers();
  // Node >= 25 ships an experimental host localStorage that shadows the
  // jsdom realm's (and is unavailable without a flag); guard so the suite
  // stays runnable on current Node versions (audit N-1).
  localStorage?.clear?.();
});

afterAll(() => server.close());

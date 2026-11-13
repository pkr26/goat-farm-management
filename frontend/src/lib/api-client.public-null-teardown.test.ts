import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { server } from "@/test/msw-server";
import { apiFetch, setAccessToken } from "./api-client";
it("safely clears a public nullable credential and leaves subsequent requests without its bearer", async () => {
  const authorizations: Array<string | null> = [];
  server.use(http.get("/api/animals", ({ request }) => { authorizations.push(request.headers.get("Authorization")); return HttpResponse.json([]); }));
  setAccessToken("public-existing-grant", 1);
  expect(() => setAccessToken(null)).not.toThrow();
  await apiFetch("/api/animals");
  expect(authorizations).toEqual([null]);
});

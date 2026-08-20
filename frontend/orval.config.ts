import { defineConfig } from "orval";

export default defineConfig({
  goatfarm: {
    input: "../shared/openapi.json",
    output: {
      target: "./src/api/generated/endpoints.ts",
      schemas: "./src/api/generated/models",
      client: "react-query",
      mode: "split",
      // Remove schemas that disappeared or were renamed in OpenAPI. Without
      // this, Orval updates the barrel but leaves stale model files behind.
      clean: true,
      override: {
        mutator: {
          path: "./src/api/custom-instance.ts",
          name: "customInstance",
        },
      },
    },
  },
});

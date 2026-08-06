import { defineConfig } from "orval";

export default defineConfig({
  goatfarm: {
    input: "../shared/openapi.json",
    output: {
      target: "./src/api/generated/endpoints.ts",
      schemas: "./src/api/generated/models",
      client: "react-query",
      mode: "split",
      override: {
        mutator: {
          path: "./src/api/custom-instance.ts",
          name: "customInstance",
        },
      },
    },
  },
});

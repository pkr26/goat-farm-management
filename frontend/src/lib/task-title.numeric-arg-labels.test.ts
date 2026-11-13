import { expect, it } from "vitest";

import { translate } from "@/lib/i18n";
import { resolveTaskTitle } from "./task-title";

it.each([0, -0, -123.5, 1e21])("renders a finite numeric duty argument (%s) through the public catalog", (tag) => {
  for (const language of ["en", "te"] as const) {
    const task = { title: "Legacy vaccine duty", title_key: "pre_kidding_vaccine", title_args: { tag } };
    expect(resolveTaskTitle(task, language)).toBe(translate(language, "taskGen.pre_kidding_vaccine", { tag }));
  }
});

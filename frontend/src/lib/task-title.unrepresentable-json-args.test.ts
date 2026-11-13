import { expect, it } from "vitest";

import { translate } from "@/lib/i18n";
import { resolveTaskTitle, type TaskTitleSource } from "./task-title";

it("keeps a usable duty label when an extra JSON argument cannot become text", () => {
  const task: TaskTitleSource = {
    title: "Routine vaccine for Goat7",
    title_key: "pre_kidding_vaccine",
    title_args: {
      tag: "Goat7",
      extra: JSON.parse('{"toString":null}') as unknown,
    },
  };
  expect(() => resolveTaskTitle(task, "en")).not.toThrow();
  expect([
    task.title,
    translate("en", "taskGen.pre_kidding_vaccine", { tag: "Goat7" }),
  ]).toContain(resolveTaskTitle(task, "en"));
});

it("retains the payload label when its required JSON argument cannot become text", () => {
  const task: TaskTitleSource = {
    title: "Routine vaccine for Goat7",
    title_key: "pre_kidding_vaccine",
    title_args: { tag: JSON.parse('{"toString":null}') as unknown },
  };
  expect(() => resolveTaskTitle(task, "en")).not.toThrow();
  expect(resolveTaskTitle(task, "en")).toBe(task.title);
});

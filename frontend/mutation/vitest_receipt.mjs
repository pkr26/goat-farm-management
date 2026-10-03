// Structured assertion and hook outcomes from the installed Vitest reporter API.
import { writeFileSync, renameSync } from "node:fs";
import { experimental_getRunnerTask } from "vitest/node";

export default class MutationReceipt {
  onTestRunEnd(modules, unhandledErrors, reason) {
    const tests = [];
    const suiteErrors = [];
    const errors = (task) => (task.result?.errors ?? []).map((error) => ({ name: error.name, message: error.message }));
    function visit(task) {
      if (task.type === "test") tests.push({ name: task.name, state: task.result?.state, hooks: task.result?.hooks ?? {}, errors: errors(task) });
      else { suiteErrors.push(...errors(task)); for (const child of task.tasks ?? []) visit(child); }
    }
    for (const testModule of modules) visit(experimental_getRunnerTask(testModule));
    const file = process.env.MUTATION_RECEIPT_PATH;
    if (!file) throw new Error("Missing MUTATION_RECEIPT_PATH");
    writeFileSync(`${file}.tmp`, JSON.stringify({ tests, suiteErrors, unhandledErrors, reason }));
    renameSync(`${file}.tmp`, file);
  }
}

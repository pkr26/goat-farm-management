import type { TaskOut } from "@/api/generated/models";

/** Product/disease hints parsed from a linked duty's title, so recorded
 * events match the vaccination templates instead of leaving both blank. */
export function taskPrefill(task: TaskOut): {
  product_name?: string;
  disease_target?: string;
} {
  // Strip auto-task scaffolding: "[Supplier #3] Day 4: …" → "…", and a
  // trailing ": <animal tag>" ("Pre-kidding ET+TT vaccine: G-ABC12").
  let title = task.title.replace(/^\[[^\]]*\]\s*/, "");
  title = title.replace(/^Days?\s*\d+(?:[–—-]\d+)?:\s*/i, "");
  if (task.animal_tag && title.endsWith(`: ${task.animal_tag}`)) {
    title = title.slice(0, -`: ${task.animal_tag}`.length);
  }
  if (task.category === "DEWORMING") {
    const drug = title.split("—")[1]?.trim();
    return { product_name: drug || undefined, disease_target: "Deworming" };
  }
  if (task.category === "VACCINE") {
    const vaccinated = /vaccinate\s+(.+?)\s*(?:\(|$)/i.exec(title);
    if (vaccinated) return { disease_target: vaccinated[1].trim() };
    if (/et\s*\+\s*tt/i.test(title)) return { disease_target: "ET + TT pre-kidding" };
    if (title.trim()) return { disease_target: title.trim() };
  }
  return {};
}

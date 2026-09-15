/**
 * Generated duty titles (H1): a payload carrying title_key/title_args renders
 * through the taskGen catalog in the active language; missing fields, unknown
 * keys and manual duties fall back to the payload's own English title. Keys
 * and args follow the final backend contract (audit_reports/2026-09-14/
 * task_title_keys.md).
 */

import { describe, expect, it } from "vitest";

import { resolveTaskTitle } from "./task-title";

describe("resolveTaskTitle", () => {
  it("renders a known key through the English catalog with arg interpolation", () => {
    expect(
      resolveTaskTitle(
        {
          title: "Kidding watch: G-101 (due 20-09) — check udder fill",
          title_key: "kidding_watch",
          title_args: { tag: "G-101", kidding_date: "2026-09-20", days_before: 3 },
        },
        "en",
      ),
    ).toBe(
      "Kidding watch: G-101 (due 20 Sep 2026) — check udder fill, tail-head ligaments, vulva discharge",
    );
  });

  it("renders the same duty in Telugu with a localized date", () => {
    const rendered = resolveTaskTitle(
      { title: "fallback", title_key: "kidding_due", title_args: { tag: "G-101" } },
      "te",
    );
    expect(rendered).toBe("ప్రసవం రానుంది: G-101");
  });

  it("renders the due-day labor watch variant when days_before is 0", () => {
    const rendered = resolveTaskTitle(
      {
        title: "fallback",
        title_key: "kidding_watch",
        title_args: { tag: "G-7", kidding_date: "2026-09-20", days_before: 0 },
      },
      "te",
    );
    expect(rendered).toContain("G-7");
    expect(rendered).toContain("రాత్రంతా ప్రసవ గమనింపు");
    expect(rendered).toContain("15–20 నిమిషాల్లో");
  });

  it("maps the contract's English month names onto localized month keys", () => {
    expect(
      resolveTaskTitle(
        {
          title: "fallback",
          title_key: "fmd_vaccination_round",
          title_args: { month: "March", year: 2027 },
        },
        "te",
      ),
    ).toContain("మార్చి 2027");
    // Numeric months work defensively too.
    expect(
      resolveTaskTitle(
        {
          title: "fallback",
          title_key: "deworming_round",
          title_args: { month: "9", year: 2026 },
        },
        "en",
      ),
    ).toContain("September 2026");
  });

  it("renders every final contract key from the catalog", () => {
    // The 40-key contract (task_title_keys.md): no key may fall back.
    const keys = [
      "pregnancy_check",
      "return_to_heat_watch",
      "pre_kidding_vaccine",
      "pre_kidding_vaccine_booster",
      "move_to_delivery",
      "move_to_pregnancy_late",
      "birthing_kit_check",
      "kidding_watch",
      "kidding_due",
      "wean_kids",
      "move_to_resting",
      "post_kidding_dam_check",
      "kidding_stall_cleanout",
      "kid_support",
      "rebreed",
      "insurance_renewal",
      "fmd_vaccination_round",
      "et_hs_premonsoon_round",
      "goat_pox_round",
      "ccpp_round",
      "deworming_round",
      "hoof_trimming_round",
      "ectoparasite_spray_round",
      "shed_disinfection_round",
      "monthly_weighing_round",
      "morning_feed_routine",
      "daily_water_check",
      "feed_reorder",
      "buck_rotation",
      "quarantine_arrival_inspection",
      "quarantine_rest",
      "quarantine_deworm",
      "quarantine_liver_tonic",
      "quarantine_ppr_vaccine",
      "quarantine_fecal_exam",
      "quarantine_et_tetanus_vaccine",
      "quarantine_goat_pox_vaccine",
      "quarantine_prerelease_review",
      "quarantine_fmd_vaccine",
      "quarantine_release",
    ];
    expect(keys).toHaveLength(40);
    for (const key of keys) {
      for (const language of ["en", "te"] as const) {
        const rendered = resolveTaskTitle(
          { title: `FALLBACK ${key}`, title_key: key, title_args: { tag: "G-1" } },
          language,
        );
        expect(rendered, `${key} (${language})`).not.toBe(`FALLBACK ${key}`);
      }
    }
  });

  it("falls back to the payload title when the key is missing or unknown", () => {
    expect(resolveTaskTitle({ title: "Clean water troughs" }, "te")).toBe(
      "Clean water troughs",
    );
    expect(
      resolveTaskTitle({ title: "X", title_key: null, title_args: { tag: "G-1" } }, "te"),
    ).toBe("X");
    expect(
      resolveTaskTitle(
        { title: "New backend duty", title_key: "brand_new_kind", title_args: {} },
        "te",
      ),
    ).toBe("New backend duty");
  });

  it("coerces non-string args and skips nulls", () => {
    expect(
      resolveTaskTitle(
        {
          title: "fallback",
          title_key: "feed_reorder",
          title_args: { ingredient: "Maize", qty_on_hand: 120, reorder_level: 50, junk: null },
        },
        "en",
      ),
    ).toBe("Reorder Maize: 120 kg on hand (reorder level 50 kg)");
  });
});

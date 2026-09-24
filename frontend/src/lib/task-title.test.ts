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

/** Mutation-hardening (2026-09-23 campaign): the English-month lookup table
 *  and its 1–12 range gate, the *_date formatting arm, and the null/undefined
 *  arg skip — every entry pinned by name. */
describe("resolveTaskTitle month and date arg contracts", () => {
  const MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  it("maps every English month name to its own translated month", () => {
    MONTHS.forEach((name) => {
      const rendered = resolveTaskTitle(
        {
          title: "fallback",
          title_key: "fmd_vaccination_round",
          title_args: { month: name, year: 2026 },
        },
        "en",
      );
      expect(rendered, name).toBe(
        `FMD vaccination round (${name} 2026) — all animals; close via a bucket/batch vaccine health event`,
      );
    });
  });

  it("translates month names to Telugu — the raw English string is not enough", () => {
    const renderTe = (month: string) =>
      resolveTaskTitle(
        { title: "fallback", title_key: "fmd_vaccination_round", title_args: { month, year: 2026 } },
        "te",
      );
    // A broken table entry falls out of the 1–12 gate and would render the
    // raw English word inside the Telugu sentence.
    expect(renderTe("January")).toContain("జనవరి");
    expect(renderTe("December")).toContain("డిసెంబర్");
  });

  it("month names are case-insensitive and numeric strings pass the range gate", () => {
    const render = (month: string | number) =>
      String(
        resolveTaskTitle(
          { title: "fallback", title_key: "fmd_vaccination_round", title_args: { month, year: 2026 } },
          "en",
        ),
      ).match(/\((.*) 2026\)/)?.[1];
    expect(render("SEPTEMBER")).toBe("September");
    expect(render("1")).toBe("January");
    expect(render("3")).toBe("March");
    expect(render("12")).toBe("December");
    // Outside 1–12 the raw value renders, never a month name.
    expect(render("0")).toBe("0");
    expect(render("13")).toBe("13");
    expect(render("vacation")).toBe("vacation");
  });

  it("formats *_date args but leaves non-date names and non-ISO values raw", () => {
    const rendered = resolveTaskTitle(
      {
        title: "fallback",
        title_key: "pregnancy_check",
        title_args: { tag: "G-1", breeding_date: "2026-02-05" },
      },
      "en",
    );
    expect(rendered).toBe("Pregnancy check: G-1 (bred 5 Feb 2026)");

    // Non-ISO values and non-date names fall through untouched.
    const raw = resolveTaskTitle(
      {
        title: "fallback",
        title_key: "pregnancy_check",
        title_args: { tag: "G-1", breeding_date: "not-a-date" },
      },
      "en",
    );
    expect(raw).toBe("Pregnancy check: G-1 (bred not-a-date)");
  });

  it("skips null and undefined args entirely", () => {
    // The null entry comes FIRST: a `break` mutant would swallow the tag.
    const rendered = resolveTaskTitle(
      {
        title: "fallback",
        title_key: "pre_kidding_vaccine",
        title_args: { month: null, count: undefined, tag: "G-1" },
      },
      "en",
    );
    expect(rendered).toBe("Pre-kidding ET+TT vaccine: G-1");
  });
});

describe("famacha_round (2026-09-23 fix)", () => {
  it("localizes the monthly FAMACHA scoring round in both languages", () => {
    const source = {
      title: "FAMACHA scoring round — English fallback",
      title_key: "famacha_round",
      title_args: {},
    } as const;
    expect(resolveTaskTitle(source, "en")).toContain("conjunctiva");
    expect(resolveTaskTitle(source, "te")).toContain("\u0c2b\u0c3e\u0c2e\u0c3e\u0c1a\u0c3e");
    expect(resolveTaskTitle(source, "te")).not.toContain("English fallback");
  });
});

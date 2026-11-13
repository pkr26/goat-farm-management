import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { SalesAssumptions } from "@/api/generated/models";

import { FestivalDateEditor, parseFestivalDates } from "./festival-date-editor";

function consecutiveDates(count: number) {
  return Array.from({ length: count }, (_, index) =>
    new Date(Date.UTC(2039, 0, index + 1)).toISOString().slice(0, 10),
  );
}

function renderEditor(initial: SalesAssumptions) {
  let latest = initial;
  const changed = vi.fn();
  const validity = vi.fn();
  function Editor() {
    const [value, setValue] = useState(initial);
    latest = value;
    return (
      <FestivalDateEditor
        value={value}
        onValidityChange={validity}
        onChange={(patch) => {
          changed(patch);
          setValue((previous) => ({ ...previous, ...patch }));
        }}
      />
    );
  }
  render(<Editor />);
  return { changed, validity, value: () => latest };
}

describe("festival date evidence contract", () => {
  it("accepts forty distinct dates and rejects the forty-first", () => {
    const dates = consecutiveDates(40);
    expect(parseFestivalDates([...dates].reverse().join(" , "))).toEqual(dates);
    expect(parseFestivalDates(consecutiveDates(41).join(","))).toBe(false);
  });

  it.each([
    "0000-01-01",
    "2044-02-30",
    "2039-13-01",
    "2039-01-00",
    "2039-01-05,",
    "2039-1-05",
    "not-a-date",
  ])("rejects a malformed or impossible date list: %s", (raw) => {
    expect(parseFestivalDates(raw)).toBe(false);
  });

  it("accepts the API's first and last supported calendar years", () => {
    expect(parseFestivalDates("9999-12-31, 0001-01-01")).toEqual(["0001-01-01", "9999-12-31"]);
  });

  it.each(["2044-02-30", "0000-01-01"])("keeps invalid draft %s visible without replacing stored dates and recovers to sorted dates", (invalidDraft) => {
    const editor = renderEditor({ festival_date_overrides: ["2039-01-05"] });
    const input = screen.getByLabelText("Confirmed festival dates (optional)");

    fireEvent.change(input, { target: { value: invalidDraft } });
    expect(input).toHaveValue(invalidDraft);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", "sim-festival-help sim-festival-error");
    expect(screen.getByRole("alert")).toHaveTextContent("Use valid, distinct YYYY-MM-DD dates (at most 40).");
    expect(editor.changed).not.toHaveBeenCalled();
    expect(editor.value().festival_date_overrides).toEqual(["2039-01-05"]);
    expect(editor.validity).toHaveBeenLastCalledWith(false);

    fireEvent.change(input, { target: { value: " 2044-02-29, 2039-12-26 " } });
    expect(input).toHaveValue(" 2044-02-29, 2039-12-26 ");
    expect(editor.changed).toHaveBeenLastCalledWith({ festival_date_overrides: ["2039-12-26", "2044-02-29"] });
    expect(editor.value().festival_date_overrides).toEqual(["2039-12-26", "2044-02-29"]);
    expect(editor.validity).toHaveBeenLastCalledWith(true);
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(input).toHaveAttribute("aria-describedby", "sim-festival-help");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps a cleared draft visible while the parent has not reconciled its stored dates", async () => {
    const changed = vi.fn();
    const validity = vi.fn();
    const stored: SalesAssumptions = { festival_date_overrides: ["2039-01-05"] };
    render(<FestivalDateEditor value={stored} onChange={changed} onValidityChange={validity} />);
    const user = userEvent.setup();
    const input = screen.getByLabelText("Confirmed festival dates (optional)");

    await user.clear(input);

    expect(input).toHaveValue("");
    expect(changed).toHaveBeenLastCalledWith({ festival_date_overrides: null });
    expect(validity).toHaveBeenLastCalledWith(true);
    expect(stored.festival_date_overrides).toEqual(["2039-01-05"]);
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("returns to the embedded calendar when confirmed dates are cleared", async () => {
    const editor = renderEditor({ festival_date_overrides: ["2039-01-05"], festival_date_source: "Village bulletin" });
    const user = userEvent.setup();
    const input = screen.getByLabelText("Confirmed festival dates (optional)");
    await user.clear(input);

    expect(input).toHaveValue("");
    expect(editor.changed).toHaveBeenLastCalledWith({ festival_date_overrides: null });
    expect(editor.value().festival_date_overrides).toBeNull();
    expect(editor.value().festival_date_source).toBe("Village bulletin");
    expect(editor.validity).toHaveBeenLastCalledWith(true);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("retains exactly five hundred provenance characters and makes a cleared source absent", async () => {
    const editor = renderEditor({ festival_date_overrides: ["2039-01-05"] });
    const user = userEvent.setup();
    const source = screen.getByLabelText("Festival date source");
    const provenance = "x".repeat(500);
    await user.type(source, provenance + "z");

    expect(source).toHaveValue(provenance);
    expect(editor.value().festival_date_source).toBe(provenance);
    expect(editor.value().festival_date_overrides).toEqual(["2039-01-05"]);
    await user.clear(source);
    expect(source).toHaveValue("");
    expect(editor.changed).toHaveBeenLastCalledWith({ festival_date_source: null });
    expect(editor.value().festival_date_source).toBeNull();
  });
});

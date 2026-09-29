/**
 * RemotePicker i18n smoke: the picker chrome that callers do NOT pass as
 * props (search placeholder default, empty/loading messaging, the status
 * line and its plural forms) resolves through the picker.remote.* catalog
 * keys — pinned here in Telugu with the language set to te.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { RemotePicker } from "@/components/remote-picker";
import { Label } from "@/components/ui/label";
import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { createTestQueryClient } from "@/test/render";

function renderTeluguPicker() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  return render(
    <LanguageProvider>
      <QueryClientProvider client={createTestQueryClient()}>
        <Label htmlFor="animal-picker">మేక</Label>
        <RemotePicker
          id="animal-picker"
          value=""
          onValueChange={() => undefined}
          placeholder="ఒక మేకను ఎంచుకోండి"
          dialogTitle="మేకను ఎంచుకోండి"
          searchLabel="మేకలు వెతకండి"
          sourcePath="/api/animals"
          cacheKey={["i18n-smoke"]}
          loadPage={async () => ({ options: [], total: 0, nextOffset: 0 })}
        />
      </QueryClientProvider>
    </LanguageProvider>,
  );
}

describe("RemotePicker — Telugu", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.lang = "en";
  });

  it("renders the default chrome and empty state in Telugu", async () => {
    const user = userEvent.setup();
    renderTeluguPicker();

    await user.click(await screen.findByRole("button", { name: "మేక" }));
    const dialog = await screen.findByRole("dialog", { name: "మేకను ఎంచుకోండి" });

    // The empty state and the search placeholder default come from
    // picker.remote.*, not from caller props.
    expect(await within(dialog).findByText("సరిపోయే ఎంపికలు లేవు.")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("మేకలు వెతకండి")).toHaveAttribute(
      "placeholder",
      "వెతకండి…",
    );
    // The status line composes its plural forms from the _one/_many keys.
    expect(
      within(dialog).getByText(
        "0 ఎంపికలు అందుబాటులో ఉన్నాయి. 0 సరిపోయే రికార్డుల్లో 0 తనిఖీ అయ్యాయి. సరిపోయే అన్ని రికార్డులు తనిఖీ అయ్యాయి.",
      ),
    ).toBeInTheDocument();
    // The empty listbox itself is option-free — the message is its sibling.
    const listbox = within(dialog).getByRole("listbox", { name: "మేకను ఎంచుకోండి ఫలితాలు" });
    expect(within(listbox).queryByText("సరిపోయే ఎంపికలు లేవు.")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("No matching options.")).not.toBeInTheDocument();
  });

  it("keeps the Telugu catalog at parity for the picker chrome keys", () => {
    expect(translate("te", "picker.remote.empty")).toBe("సరిపోయే ఎంపికలు లేవు.");
    expect(translate("te", "picker.remote.searching")).toBe("వెతుకుతోంది…");
    expect(translate("te", "picker.remote.statusOptions_one", { count: 1 })).toBe(
      "1 ఎంపిక అందుబాటులో ఉంది.",
    );
    expect(translate("te", "picker.remote.statusChecked_many", { checked: 50, total: 201 })).toBe(
      "201 సరిపోయే రికార్డుల్లో 50 తనిఖీ అయ్యాయి.",
    );
  });
});

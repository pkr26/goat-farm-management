import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { Label } from "@/components/ui/label";
import { LanguageProvider } from "@/lib/i18n";
import { createTestQueryClient } from "@/test/render";
import { RemotePicker, type RemotePickerPage } from "./remote-picker";

it.each([
  { total: Number.NaN, nextOffset: 1 },
  { total: 10, nextOffset: Number.NaN },
  { total: Number.POSITIVE_INFINITY, nextOffset: 1 },
  { total: 10, nextOffset: Number.POSITIVE_INFINITY },
  { total: -1, nextOffset: 1 },
  { total: 10, nextOffset: -1 },
])("keeps available options usable without announcing invalid record counts: %j", async ({ total, nextOffset }) => {
  localStorage.setItem("herdly.language", "en");
  const client = createTestQueryClient();
  const onValueChange = vi.fn();
  const loadPage = async (): Promise<RemotePickerPage> => ({ options: [{ value: "9", label: "Eligible goat" }], total, nextOffset });
  const view = render(<QueryClientProvider client={client}><LanguageProvider>
    <Label htmlFor="invalid-count-picker">Target</Label>
    <RemotePicker id="invalid-count-picker" value="" onValueChange={onValueChange} placeholder="Choose target"
      dialogTitle="Choose a target" searchLabel="Search targets" sourcePath="/api/count-regression-options" cacheKey={["invalid-count-regression"]}
      loadPage={loadPage} />
  </LanguageProvider></QueryClientProvider>);
  try {
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Target" }));
    expect(await screen.findByRole("option", { name: "Eligible goat" })).toBeVisible();
    expect(document.getElementById("invalid-count-picker-picker-status")).toHaveTextContent(/^1 option available\.$/);
    await user.click(screen.getByRole("option", { name: "Eligible goat" }));
    expect(onValueChange).toHaveBeenCalledExactlyOnceWith("9");
    expect(screen.queryByRole("dialog")).toBeNull();
  } finally { view.unmount(); client.clear(); localStorage.clear(); }
});

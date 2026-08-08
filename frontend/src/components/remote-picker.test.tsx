import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  RemotePicker,
  type RemotePickerLoadArgs,
  type RemotePickerPage,
} from "@/components/remote-picker";
import { Label } from "@/components/ui/label";
import { createTestQueryClient } from "@/test/render";

function renderPicker({
  loadPage,
  initialValue = "",
  searchMaxLength,
}: {
  loadPage: (args: RemotePickerLoadArgs) => Promise<RemotePickerPage>;
  initialValue?: string;
  searchMaxLength?: number;
}) {
  function Harness() {
    const [value, setValue] = useState(initialValue);
    return (
      <QueryClientProvider client={createTestQueryClient()}>
        <Label htmlFor="animal-picker">Animal</Label>
        <RemotePicker
          id="animal-picker"
          value={value}
          onValueChange={setValue}
          selectedOption={
            initialValue ? { value: initialValue, label: `Previously selected ${initialValue}` } : null
          }
          placeholder="Pick an animal"
          dialogTitle="Choose an animal"
          searchLabel="Search animals"
          searchMaxLength={searchMaxLength}
          sourcePath="/api/animals"
          cacheKey={["test"]}
          loadPage={loadPage}
          noEligibleYetMessage="No eligible animals checked yet. Load more to continue."
        />
        <output aria-label="chosen value">{value}</output>
      </QueryClientProvider>
    );
  }

  return Harness;
}

describe("RemotePicker", () => {
  it("loads past 200 source records without falsely reporting an exhausted eligible list", async () => {
    const user = userEvent.setup();
    const loadPage = vi.fn(async ({ offset, limit }: RemotePickerLoadArgs) => {
      const last = offset === 200;
      return {
        options: last ? [{ value: "201", label: "G-0201 · Late doe" }] : [],
        total: 201,
        nextOffset: Math.min(offset + limit, 201),
      };
    });
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByText(/No eligible animals checked yet/)).toBeInTheDocument();
    expect(within(dialog).queryByText("No matching options.")).not.toBeInTheDocument();

    for (let page = 0; page < 4; page += 1) {
      await user.click(within(dialog).getByRole("button", { name: "Load more" }));
      await waitFor(() => expect(loadPage).toHaveBeenCalledTimes(page + 2));
    }

    expect(await within(dialog).findByRole("option", { name: /G-0201 · Late doe/ })).toBeInTheDocument();
    expect(within(dialog).getByText(/Checked 201 of 201/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    expect(loadPage.mock.calls.map(([args]) => args.offset)).toEqual([0, 50, 100, 150, 200]);
  });

  it("searches remotely and keeps the chosen label when a later search excludes it", async () => {
    const user = userEvent.setup();
    const loadPage = vi.fn(async ({ query }: RemotePickerLoadArgs) => ({
      options:
        query === "other"
          ? [{ value: "2", label: "G-0002 · Other" }]
          : [{ value: "1", label: "G-0001 · Nila" }],
      total: 1,
      nextOffset: 1,
    }));
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const firstDialog = screen.getByRole("dialog", { name: "Choose an animal" });
    await user.click(await within(firstDialog).findByRole("option", { name: /G-0001 · Nila/ }));
    expect(screen.getByLabelText("chosen value")).toHaveTextContent("1");
    expect(screen.getByLabelText("Animal")).toHaveTextContent("G-0001 · Nila");

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const secondDialog = screen.getByRole("dialog", { name: "Choose an animal" });
    await user.type(within(secondDialog).getByLabelText("Search animals"), "other");
    expect(await within(secondDialog).findByRole("option", { name: /G-0002 · Other/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Animal")).toHaveTextContent("G-0001 · Nila");
    await waitFor(() =>
      expect(loadPage).toHaveBeenLastCalledWith(expect.objectContaining({ query: "other" })),
    );
  });

  it("shows an exhausted empty state and lets the user retry an initial error", async () => {
    const user = userEvent.setup();
    let fails = true;
    const loadPage = vi.fn(async () => {
      if (fails) throw new Error("offline");
      return { options: [], total: 0, nextOffset: 0 };
    });
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Could not load options");

    fails = false;
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByText("No matching options.")).toBeInTheDocument();
    expect(within(dialog).getByText(/Checked 0 of 0/)).toBeInTheDocument();
  });

  it("keeps a checked empty prefix visible when loading the next page fails", async () => {
    const user = userEvent.setup();
    let failNextPage = true;
    const loadPage = vi.fn(async ({ offset }: RemotePickerLoadArgs) => {
      if (offset === 50 && failNextPage) throw new Error("next page offline");
      return offset === 0
        ? { options: [], total: 100, nextOffset: 50 }
        : {
            options: [{ value: "51", label: "G-0051 · Reached" }],
            total: 100,
            nextOffset: 100,
          };
    });
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByText(/No eligible animals checked yet/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Load more" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The next page could not be loaded",
    );
    expect(within(dialog).queryByText("Could not load options.")).not.toBeInTheDocument();
    expect(within(dialog).getByText(/No eligible animals checked yet/)).toBeInTheDocument();

    failNextPage = false;
    await user.click(within(dialog).getByRole("button", { name: "Load more" }));
    expect(await within(dialog).findByRole("option", { name: /G-0051 · Reached/ })).toBeInTheDocument();
  });

  it("preserves a prefilled value that is not present in loaded pages", () => {
    const Harness = renderPicker({
      initialValue: "999",
      loadPage: async () => ({ options: [], total: 0, nextOffset: 0 }),
    });
    render(<Harness />);

    expect(screen.getByLabelText("Animal")).toHaveTextContent("Previously selected 999");
    expect(screen.getByLabelText("chosen value")).toHaveTextContent("999");
  });

  it("bounds the server query before sending it", async () => {
    const user = userEvent.setup();
    const loadPage = vi.fn(async () => ({ options: [], total: 0, nextOffset: 0 }));
    const Harness = renderPicker({ loadPage, searchMaxLength: 3 });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const search = screen.getByLabelText("Search animals");
    await user.type(search, "abcdef");

    expect(search).toHaveValue("abc");
    await waitFor(() =>
      expect(loadPage).toHaveBeenLastCalledWith(expect.objectContaining({ query: "abc" })),
    );
  });

  it("supports arrow, Home, End and Enter navigation and returns focus on Escape", async () => {
    const user = userEvent.setup();
    const Harness = renderPicker({
      loadPage: async () => ({
        options: [
          { value: "1", label: "G-0001 · Nila" },
          { value: "2", label: "G-0002 · Tara" },
        ],
        total: 2,
        nextOffset: 2,
      }),
    });
    render(<Harness />);

    const trigger = screen.getByRole("combobox", { name: "Animal" });
    await user.click(trigger);
    const search = screen.getByLabelText("Search animals");
    const first = await screen.findByRole("option", { name: /G-0001 · Nila/ });
    const second = screen.getByRole("option", { name: /G-0002 · Tara/ });

    search.focus();
    await user.keyboard("{ArrowDown}");
    expect(first).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(second).toHaveFocus();
    await user.keyboard("{Home}");
    expect(first).toHaveFocus();
    await user.keyboard("{End}{Enter}");
    expect(screen.getByLabelText("chosen value")).toHaveTextContent("2");

    await user.click(trigger);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});

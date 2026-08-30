import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  RemotePicker,
  type RemotePickerLoadArgs,
  type RemotePickerOption,
  type RemotePickerPage,
} from "@/components/remote-picker";
import { Label } from "@/components/ui/label";
import { createTestQueryClient } from "@/test/render";

function renderPicker({
  loadPage,
  initialValue = "",
  pageSize,
  searchMaxLength,
  staticOptions,
}: {
  loadPage: (args: RemotePickerLoadArgs) => Promise<RemotePickerPage>;
  initialValue?: string;
  pageSize?: number;
  searchMaxLength?: number;
  staticOptions?: RemotePickerOption[];
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
          staticOptions={staticOptions}
          sourcePath="/api/animals"
          cacheKey={["test"]}
          loadPage={loadPage}
          pageSize={pageSize}
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
    expect(within(dialog).getByText(
      "0 options available. Checked 50 of 201 matching records. More records are available.",
    )).toBeInTheDocument();
    expect(within(dialog).queryByText("No matching options.")).not.toBeInTheDocument();

    for (let page = 0; page < 4; page += 1) {
      await user.click(within(dialog).getByRole("button", { name: "Load more" }));
      await waitFor(() => expect(loadPage).toHaveBeenCalledTimes(page + 2));
    }

    expect(await within(dialog).findByRole("option", { name: /G-0201 · Late doe/ })).toBeInTheDocument();
    expect(within(dialog).getByText(
      "1 option available. Checked 201 of 201 matching records. All matching records checked.",
    )).toBeInTheDocument();
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

  it("sends one search request for a typed term, not one per keystroke", async () => {
    const user = userEvent.setup();
    const queries: string[] = [];
    const loadPage = vi.fn(async ({ query }: RemotePickerLoadArgs) => {
      queries.push(query);
      return { options: [{ value: "12", label: "G-0012 · Nila" }], total: 1, nextOffset: 1 };
    });
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    await within(dialog).findByRole("option", { name: /G-0012 · Nila/ });

    await user.type(within(dialog).getByLabelText("Search animals"), "G-0012");

    await waitFor(() => expect(queries).toContain("G-0012"));
    // Previously: one request per committed keystroke ("G", "G-", "G-0", …).
    expect(queries).toEqual(["", "G-0012"]);
  });

  it("does not load another page for the old term while a search is settling", async () => {
    const user = userEvent.setup();
    const requests: Array<{ query: string; offset: number }> = [];
    const loadPage = vi.fn(async ({ query, offset }: RemotePickerLoadArgs) => {
      requests.push({ query, offset });
      return query
        ? { options: [], total: 0, nextOffset: 0 }
        : { options: [], total: 100, nextOffset: 50 };
    });
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByRole("button", { name: "Load more" })).toBeEnabled();

    await user.type(within(dialog).getByLabelText("Search animals"), "Nila");

    expect(within(dialog).queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("listbox")).toHaveAttribute("aria-busy", "true");
    await waitFor(() => expect(requests).toContainEqual({ query: "Nila", offset: 0 }));
    expect(requests).not.toContainEqual({ query: "", offset: 50 });
  });

  it("trims searches and resets both the input and request when closed", async () => {
    const user = userEvent.setup();
    const queries: string[] = [];
    const loadPage = vi.fn(async ({ query }: RemotePickerLoadArgs) => {
      queries.push(query);
      return { options: [], total: 0, nextOffset: 0 };
    });
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    const trigger = screen.getByRole("combobox", { name: "Animal" });
    await user.click(trigger);
    const search = screen.getByLabelText("Search animals");
    expect(search).toHaveAttribute("placeholder", "Search…");
    await user.type(search, "  Nila  ");
    await waitFor(() => expect(queries).toContain("Nila"));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(trigger);
    expect(screen.getByLabelText("Search animals")).toHaveValue("");
    await waitFor(() => expect(loadPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: "" }),
    ));
  });

  it("keeps the loaded options on screen while the next search is in flight", async () => {
    const user = userEvent.setup();
    let releaseSearch: (() => void) | undefined;
    const searchInFlight = new Promise<void>((resolve) => {
      releaseSearch = resolve;
    });
    const loadPage = vi.fn(async ({ query }: RemotePickerLoadArgs) => {
      if (query) await searchInFlight;
      return {
        options: query
          ? [{ value: "2", label: "G-0002 · Tara" }]
          : [{ value: "1", label: "G-0001 · Nila" }],
        total: 1,
        nextOffset: 1,
      };
    });
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    await within(dialog).findByRole("option", { name: /G-0001 · Nila/ });

    await user.type(within(dialog).getByLabelText("Search animals"), "Tara");
    await waitFor(() =>
      expect(loadPage).toHaveBeenLastCalledWith(expect.objectContaining({ query: "Tara" })),
    );

    // Previously the fresh query key had no data, so the whole listbox was
    // replaced by "Loading options…" and nothing was clickable while typing.
    expect(within(dialog).getByRole("option", { name: /G-0001 · Nila/ })).toBeInTheDocument();
    expect(within(dialog).queryByText("Loading options…")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("listbox")).toHaveAttribute("aria-busy", "true");

    releaseSearch?.();
    expect(
      await within(dialog).findByRole("option", { name: /G-0002 · Tara/ }),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("listbox")).not.toHaveAttribute("aria-busy");
  });

  it("marks the listbox busy during the initial request", async () => {
    const user = userEvent.setup();
    let release: (() => void) | undefined;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    const Harness = renderPicker({
      loadPage: async () => {
        await pending;
        return {
          options: [{ value: "1", label: "G-0001 · Nila" }],
          total: 1,
          nextOffset: 1,
        };
      },
    });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const listbox = screen.getByRole("listbox", { name: "Choose an animal results" });
    expect(listbox).toHaveAttribute("aria-busy", "true");

    release?.();
    expect(await screen.findByRole("option", { name: /G-0001 · Nila/ })).toBeInTheDocument();
    expect(listbox).not.toHaveAttribute("aria-busy");
  });

  it("aborts an in-flight page when the picker closes", async () => {
    const user = userEvent.setup();
    let requestSignal: AbortSignal | undefined;
    const Harness = renderPicker({
      loadPage: ({ signal }) => {
        requestSignal = signal;
        return new Promise<RemotePickerPage>(() => undefined);
      },
    });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    await waitFor(() => expect(requestSignal).toBeDefined());
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    expect(requestSignal?.aborted).toBe(true);
  });

  it("shows an exhausted empty state and lets the user retry an initial error", async () => {
    const user = userEvent.setup();
    let fails = true;
    let releaseRetry: (() => void) | undefined;
    const retryPending = new Promise<void>((resolve) => {
      releaseRetry = resolve;
    });
    const loadPage = vi.fn(async () => {
      if (fails) throw new Error("offline");
      await retryPending;
      return { options: [], total: 0, nextOffset: 0 };
    });
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Could not load options");

    fails = false;
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(within(dialog).getByRole("listbox")).toHaveAttribute("aria-busy", "true");
    expect(within(dialog).getByText("Loading options…")).toBeInTheDocument();
    releaseRetry?.();
    expect(await within(dialog).findByText("No matching options.")).toBeInTheDocument();
    expect(within(dialog).getByText(/Checked 0 of 0/)).toBeInTheDocument();
  });

  it("warns when cached options cannot be refreshed", async () => {
    const user = userEvent.setup();
    let failRefresh = false;
    const loadPage = vi.fn(async () => {
      if (failRefresh) throw new Error("offline");
      return {
        options: [{ value: "1", label: "G-0001 · Nila" }],
        total: 1,
        nextOffset: 1,
      };
    });
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    const trigger = screen.getByRole("combobox", { name: "Animal" });
    await user.click(trigger);
    expect(await screen.findByRole("option", { name: /G-0001 · Nila/ })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    failRefresh = true;
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not refresh options. Showing the previously loaded results.",
    );
    expect(within(dialog).getByRole("option", { name: /G-0001 · Nila/ })).toBeInTheDocument();
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

  it("stops pagination when a source page does not advance its offset", async () => {
    const user = userEvent.setup();
    const loadPage = vi.fn(async () => ({ options: [], total: 100, nextOffset: 0 }));
    const Harness = renderPicker({ loadPage });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByText("No matching options.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    expect(loadPage).toHaveBeenCalledOnce();
  });

  it("deduplicates remote pages and lets the canonical remote option replace a static collision", async () => {
    const user = userEvent.setup();
    const loadPage = vi.fn(async ({ offset }: RemotePickerLoadArgs) =>
      offset === 0
        ? {
            options: [
              { value: "1", label: "Remote one" },
              { value: "1", label: "Duplicate remote one" },
            ],
            total: 60,
            nextOffset: 50,
          }
        : {
            options: [
              { value: "1", label: "Remote one again" },
              { value: "2", label: "Remote two" },
            ],
            total: 60,
            nextOffset: 60,
          },
    );
    const Harness = renderPicker({
      loadPage,
      staticOptions: [
        { value: "1", label: "Stale static one" },
        { value: "3", label: "Static three" },
      ],
    });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByRole("option", { name: "Remote one" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: /Duplicate|Stale/ })).not.toBeInTheDocument();
    expect(within(dialog).getAllByRole("option")).toHaveLength(2);

    await user.click(within(dialog).getByRole("button", { name: "Load more" }));
    expect(await within(dialog).findByRole("option", { name: "Remote two" })).toBeInTheDocument();
    expect(within(dialog).getAllByRole("option")).toHaveLength(3);
    expect(within(dialog).getAllByRole("option", { name: /Remote one/ })).toHaveLength(1);
  });

  it("does not claim zero options while a selectable static option is rendered", async () => {
    // The tasks/finance pickers pass staticOptions=[{value: NONE, "— none —"}].
    // Previously a search with zero server matches rendered that selectable
    // option and, directly beneath it, "No matching options." plus a status
    // line of "0 options available." — contradicting what the user sees.
    const user = userEvent.setup();
    const loadPage = vi.fn(async () => ({ options: [], total: 0, nextOffset: 0 }));
    const Harness = renderPicker({
      loadPage,
      staticOptions: [
        { value: "NONE", label: "— none —" },
        { value: "NONE", label: "duplicate none" },
      ],
    });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    // Wait for the (empty) server page to settle before asserting on the
    // empty-state block, which only renders once loading finishes.
    await within(dialog).findByText(/Checked 0 of 0/);

    expect(within(dialog).getByRole("option", { name: "— none —" })).toBeInTheDocument();
    expect(within(dialog).getAllByRole("option")).toHaveLength(1);
    expect(within(dialog).queryByRole("option", { name: "duplicate none" })).not.toBeInTheDocument();
    expect(within(dialog).queryByText("No matching options.")).not.toBeInTheDocument();
    expect(within(dialog).getByText(/1 option available/)).toBeInTheDocument();
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

  it("falls back to the raw identifier when supplied selection metadata is stale", () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <Label htmlFor="stale-selection-picker">Animal</Label>
        <RemotePicker
          id="stale-selection-picker"
          value="999"
          onValueChange={() => undefined}
          selectedOption={{ value: "998", label: "Wrong cached animal" }}
          placeholder="Pick an animal"
          dialogTitle="Choose an animal"
          searchLabel="Search animals"
          sourcePath="/api/animals"
          cacheKey={["stale-selection"]}
          loadPage={async () => ({ options: [], total: 0, nextOffset: 0 })}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByRole("combobox", { name: "Animal" })).toHaveTextContent(
      "Selected item 999",
    );
    expect(screen.queryByText("Wrong cached animal")).not.toBeInTheDocument();
  });

  it("forwards the exact option object through both selection callbacks", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    const onOptionChange = vi.fn();
    const option = { value: "1", label: "G-0001 · Nila", description: "Milking doe" };
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <Label htmlFor="callback-picker">Animal</Label>
        <RemotePicker
          id="callback-picker"
          value=""
          onValueChange={onValueChange}
          onOptionChange={onOptionChange}
          placeholder="Pick an animal"
          dialogTitle="Choose an animal"
          searchLabel="Search animals"
          sourcePath="/api/animals"
          cacheKey={["callback"]}
          loadPage={async () => ({ options: [option], total: 1, nextOffset: 1 })}
        />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    await user.click(await screen.findByRole("option", { name: /G-0001 · Nila/ }));

    expect(onOptionChange).toHaveBeenCalledWith(option);
    expect(onValueChange).toHaveBeenCalledWith("1");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("exposes the current option, roving tab stop, description, and selection indicator", async () => {
    const user = userEvent.setup();
    const Harness = renderPicker({
      initialValue: "1",
      loadPage: async () => ({
        options: [
          { value: "1", label: "G-0001 · Nila", description: "Milking doe" },
          { value: "2", label: "G-0002 · Tara" },
        ],
        total: 2,
        nextOffset: 2,
      }),
    });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(within(dialog).getByText(
      "Search the farm records, then choose one option.",
    )).toBeInTheDocument();
    const selected = await within(dialog).findByRole("option", { name: /G-0001 · Nila/ });
    const unselected = within(dialog).getByRole("option", { name: /G-0002 · Tara/ });
    expect(selected).toHaveAttribute("id", "animal-picker-picker-results-option-0");
    expect(unselected).toHaveAttribute("id", "animal-picker-picker-results-option-1");
    expect(selected).toHaveAttribute("aria-selected", "true");
    expect(selected).toHaveAttribute("tabindex", "0");
    expect(selected.querySelector(".lucide-check")).not.toHaveClass("invisible");
    expect(unselected).toHaveAttribute("aria-selected", "false");
    expect(unselected).toHaveAttribute("tabindex", "-1");
    expect(unselected.querySelector(".lucide-check")).toHaveClass("invisible");
    expect(within(selected).getByText("Milking doe")).toHaveClass("text-muted-foreground");
    expect(within(unselected).queryByText("Milking doe")).not.toBeInTheDocument();
    expect(within(dialog).getByText(
      "2 options available. Checked 2 of 2 matching records. All matching records checked.",
    )).toBeInTheDocument();
  });

  it("forwards validation relationships and keeps the selected value as the trigger description", () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <Label htmlFor="validated-picker">Animal</Label>
        <RemotePicker
          id="validated-picker"
          value="9"
          onValueChange={() => undefined}
          selectedOption={{ value: "9", label: "G-0009 · Selected" }}
          placeholder="Pick an animal"
          dialogTitle="Choose an animal"
          searchLabel="Search animals"
          sourcePath="/api/animals"
          cacheKey={["validated"]}
          loadPage={async () => ({ options: [], total: 0, nextOffset: 0 })}
          className="custom-picker"
          aria-invalid
          aria-describedby="animal-error"
        />
        <p id="animal-error">Animal is required</p>
      </QueryClientProvider>,
    );

    const trigger = screen.getByRole("combobox", { name: "Animal" });
    expect(trigger).toHaveClass("custom-picker");
    expect(trigger).toHaveAttribute("aria-invalid", "true");
    expect(trigger).toHaveAttribute(
      "aria-describedby",
      "animal-error validated-picker-picker-value",
    );
    expect(trigger).toHaveAccessibleDescription("Animal is required G-0009 · Selected");
    expect(trigger).toHaveAttribute("aria-controls", "validated-picker-picker-dialog");
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

  it.each([
    { requested: 1, expected: 10 },
    { requested: 20.9, expected: 20 },
    { requested: 101, expected: 100 },
    { requested: Number.NaN, expected: 50 },
  ])("normalizes a page size of $requested to $expected", async ({ requested, expected }) => {
    const user = userEvent.setup();
    const loadPage = vi.fn(async () => ({ options: [], total: 0, nextOffset: 0 }));
    const Harness = renderPicker({ loadPage, pageSize: requested });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    await waitFor(() => expect(loadPage).toHaveBeenCalledOnce());
    expect(loadPage).toHaveBeenCalledWith(expect.objectContaining({ limit: expected }));
  });

  it("closes an open picker when it becomes disabled", async () => {
    const user = userEvent.setup();
    const client = createTestQueryClient();

    function Picker({ disabled }: { disabled: boolean }) {
      return (
        <QueryClientProvider client={client}>
          <Label htmlFor="dynamic-picker">Animal</Label>
          <RemotePicker
            id="dynamic-picker"
            value=""
            onValueChange={vi.fn()}
            placeholder="Pick an animal"
            dialogTitle="Choose an animal"
            searchLabel="Search animals"
            sourcePath="/api/animals"
            cacheKey={["dynamic-disabled-test"]}
            loadPage={async () => ({ options: [], total: 0, nextOffset: 0 })}
            disabled={disabled}
          />
        </QueryClientProvider>
      );
    }

    const view = render(<Picker disabled={false} />);
    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    expect(screen.getByRole("dialog", { name: "Choose an animal" })).toBeInTheDocument();

    view.rerender(<Picker disabled />);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("combobox", { name: "Animal" })).toBeDisabled();

    view.rerender(<Picker disabled={false} />);
    expect(screen.getByRole("combobox", { name: "Animal" })).toBeEnabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("makes a non-first selected option the sole initial roving tab stop", async () => {
    const user = userEvent.setup();
    const Harness = renderPicker({
      initialValue: "2",
      loadPage: async () => ({
        options: [
          { value: "1", label: "G-0001 · Nila" },
          { value: "2", label: "G-0002 · Tara" },
          { value: "3", label: "G-0003 · Mira" },
        ],
        total: 3,
        nextOffset: 3,
      }),
    });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const first = await screen.findByRole("option", { name: /G-0001 · Nila/ });
    const selected = screen.getByRole("option", { name: /G-0002 · Tara/ });
    const last = screen.getByRole("option", { name: /G-0003 · Mira/ });

    expect(first).toHaveAttribute("tabindex", "-1");
    expect(selected).toHaveAttribute("tabindex", "0");
    expect(last).toHaveAttribute("tabindex", "-1");
  });

  it("supports arrow, Home, End and Enter navigation and returns focus on Escape", async () => {
    const user = userEvent.setup();
    const Harness = renderPicker({
      loadPage: async () => ({
        options: [
          { value: "1", label: "G-0001 · Nila" },
          { value: "2", label: "G-0002 · Tara" },
          { value: "3", label: "G-0003 · Mira" },
        ],
        total: 3,
        nextOffset: 3,
      }),
    });
    render(<Harness />);

    const trigger = screen.getByRole("combobox", { name: "Animal" });
    await user.click(trigger);
    const search = screen.getByLabelText("Search animals");
    const first = await screen.findByRole("option", { name: /G-0001 · Nila/ });
    const second = screen.getByRole("option", { name: /G-0002 · Tara/ });
    const third = screen.getByRole("option", { name: /G-0003 · Mira/ });
    const listbox = screen.getByRole("listbox", {
      name: "Choose an animal results",
    });
    expect(first).toHaveAttribute("tabindex", "0");
    expect(second).toHaveAttribute("tabindex", "-1");
    expect(third).toHaveAttribute("tabindex", "-1");
    expect(search).toHaveAttribute(
      "aria-describedby",
      "animal-picker-picker-status",
    );
    expect(screen.getByRole("dialog", { name: "Choose an animal" })).toHaveAttribute(
      "id",
      "animal-picker-picker-dialog",
    );

    search.focus();
    expect(fireEvent.keyDown(search, { key: "ArrowDown" })).toBe(false);
    expect(first).toHaveFocus();
    await user.keyboard("x");
    expect(first).toHaveFocus();
    expect(fireEvent.keyDown(listbox, { key: "ArrowUp" })).toBe(false);
    expect(third).toHaveFocus();
    // tabIndex stays React-controlled (the selected/first option is the tab
    // stop); roving focus moves via .focus() alone, never DOM mutation.
    await user.keyboard("{ArrowDown}");
    expect(first).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(second).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(first).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(second).toHaveFocus();
    await user.keyboard("{Home}");
    expect(first).toHaveFocus();
    await user.keyboard("{End}{Enter}");
    expect(screen.getByLabelText("chosen value")).toHaveTextContent("3");

    await user.click(trigger);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("does not consume option-navigation keys when the list is empty", async () => {
    const user = userEvent.setup();
    const Harness = renderPicker({
      loadPage: async () => ({ options: [], total: 0, nextOffset: 0 }),
    });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const listbox = screen.getByRole("listbox", { name: "Choose an animal results" });
    await screen.findByText("No matching options.");

    expect(fireEvent.keyDown(listbox, { key: "ArrowDown" })).toBe(true);
    expect(document.activeElement).not.toHaveAttribute("role", "option");
  });

  it("labels and disables the load-more action while its request is pending", async () => {
    const user = userEvent.setup();
    let releaseNextPage: (() => void) | undefined;
    const nextPagePending = new Promise<void>((resolve) => {
      releaseNextPage = resolve;
    });
    const Harness = renderPicker({
      loadPage: async ({ offset }) => {
        if (offset > 0) await nextPagePending;
        return offset === 0
          ? { options: [], total: 60, nextOffset: 50 }
          : {
              options: [{ value: "51", label: "G-0051 · Reached" }],
              total: 60,
              nextOffset: 60,
            };
      },
    });
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const loadMore = await screen.findByRole("button", { name: "Load more" });
    await user.click(loadMore);

    expect(screen.getByRole("button", { name: "Loading more…" })).toBeDisabled();
    releaseNextPage?.();
    expect(await screen.findByRole("option", { name: /G-0051 · Reached/ })).toBeInTheDocument();
  });
});

/**
 * Branch coverage for RemotePicker: the trigger's label/ARIA contract, the
 * disabled-watcher timer, and the status/empty-state wording each picker in
 * the app inherits. remote-picker.test.tsx covers the paging and search
 * behaviour; this file pins the states those flows pass through too quickly
 * to observe.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ComponentProps, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { RemotePicker, type RemotePickerLoadArgs } from "@/components/remote-picker";
import { Label } from "@/components/ui/label";
import { createTestQueryClient } from "@/test/render";

type PickerProps = ComponentProps<typeof RemotePicker>;

/** A picker configured the way the app configures one, with only the props a
 * case actually varies spelled out. */
function pickerProps(overrides: Partial<PickerProps> = {}): PickerProps {
  return {
    id: "animal-picker",
    value: "",
    onValueChange: () => undefined,
    placeholder: "Pick an animal",
    dialogTitle: "Choose an animal",
    searchLabel: "Search animals",
    sourcePath: "/api/animals",
    cacheKey: ["branches"],
    loadPage: async () => ({ options: [], total: 0, nextOffset: 0 }),
    ...overrides,
  };
}

function renderPicker(overrides: Partial<PickerProps> = {}) {
  const props = pickerProps(overrides);
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <Label htmlFor={props.id}>Animal</Label>
      <RemotePicker {...props} />
    </QueryClientProvider>,
  );
}

/** Same picker, but owning its value the way a form field does, so a chosen
 * option flows back in as the new value. */
function renderStatefulPicker(overrides: Partial<PickerProps> = {}) {
  const props = pickerProps(overrides);
  function Tree() {
    const [value, setValue] = useState(props.value);
    return (
      <QueryClientProvider client={createTestQueryClient()}>
        <Label htmlFor={props.id}>Animal</Label>
        <RemotePicker {...props} value={value} onValueChange={setValue} />
      </QueryClientProvider>
    );
  }
  return render(<Tree />);
}

/** Lets pending macrotasks (the disabled watcher's close timer) run. */
async function flushTimers() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 5));
  });
}

describe("RemotePicker trigger and status states", () => {
  it("keeps a picker the user opens in the first task after mount open", async () => {
    renderPicker();

    // Nothing is awaited between mounting and the click: an unconditionally
    // scheduled close timer would still be pending here and would shut the
    // dialog the user just opened.
    fireEvent.click(screen.getByRole("combobox", { name: "Animal" }));
    expect(screen.getByRole("dialog", { name: "Choose an animal" })).toBeInTheDocument();

    await flushTimers();
    expect(screen.getByRole("dialog", { name: "Choose an animal" })).toBeInTheDocument();
  });

  it("does not close a re-enabled picker with the timer left by its disabled render", async () => {
    const client = createTestQueryClient();

    function Tree({ disabled }: { disabled: boolean }) {
      return (
        <QueryClientProvider client={client}>
          <Label htmlFor="reenabled-picker">Animal</Label>
          <RemotePicker {...pickerProps({ id: "reenabled-picker", disabled })} />
        </QueryClientProvider>
      );
    }

    const view = render(<Tree disabled />);
    const disabledTrigger = screen.getByRole("combobox", { name: "Animal" });
    expect(disabledTrigger).toBeDisabled();
    expect(disabledTrigger).toHaveAttribute("aria-expanded", "false");

    // Re-enabled and opened inside the window the disabled render's close
    // timer is still armed: the effect cleanup has to cancel it.
    view.rerender(<Tree disabled={false} />);
    fireEvent.click(screen.getByRole("combobox", { name: "Animal" }));
    expect(screen.getByRole("dialog", { name: "Choose an animal" })).toBeInTheDocument();

    await flushTimers();
    expect(screen.getByRole("dialog", { name: "Choose an animal" })).toBeInTheDocument();
  });

  it("labels a value held by a later static option", () => {
    renderPicker({
      id: "static-picker",
      value: "NONE",
      staticOptions: [
        { value: "ALL", label: "All animals" },
        { value: "NONE", label: "— none —" },
      ],
    });

    const trigger = screen.getByRole("combobox", { name: "Animal" });
    expect(trigger).toHaveTextContent("— none —");
    expect(trigger).not.toHaveTextContent("All animals");
    expect(trigger).not.toHaveTextContent("Selected item NONE");
  });

  it("re-labels the trigger when the form replaces the chosen value", async () => {
    const user = userEvent.setup();

    function Tree() {
      const [value, setValue] = useState("");
      return (
        <QueryClientProvider client={createTestQueryClient()}>
          <Label htmlFor="reset-picker">Animal</Label>
          <RemotePicker
            {...pickerProps({
              id: "reset-picker",
              staticOptions: [{ value: "NONE", label: "— none —" }],
              loadPage: async () => ({
                options: [{ value: "1", label: "G-0001 · Nila" }],
                total: 1,
                nextOffset: 1,
              }),
            })}
            value={value}
            onValueChange={setValue}
          />
          <button type="button" onClick={() => setValue("NONE")}>
            Clear the animal
          </button>
        </QueryClientProvider>
      );
    }

    render(<Tree />);
    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    await user.click(await screen.findByRole("option", { name: /G-0001 · Nila/ }));
    expect(screen.getByRole("combobox", { name: "Animal" })).toHaveTextContent("G-0001 · Nila");

    // The chosen option is remembered only while it still matches the form's
    // value; a reset elsewhere in the form must not leave its label behind.
    await user.click(screen.getByRole("button", { name: "Clear the animal" }));
    const trigger = screen.getByRole("combobox", { name: "Animal" });
    expect(trigger).toHaveTextContent("— none —");
    expect(trigger).not.toHaveTextContent("G-0001 · Nila");
  });

  it("wires the trigger's expanded, invalid and description state", async () => {
    const user = userEvent.setup();
    renderPicker();
    const trigger = screen.getByRole("combobox", { name: "Animal" });

    expect(trigger).toHaveClass("w-full", "justify-between", "font-normal");
    expect(trigger).toHaveAttribute("aria-haspopup", "dialog");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).not.toHaveAttribute("aria-invalid");
    // Without a caller-supplied description the value span is the only entry,
    // with no empty token ahead of it.
    expect(trigger).toHaveAttribute("aria-describedby", "animal-picker-picker-value");

    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "Choose an animal" })).toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });

  it("mutes the placeholder and un-mutes a chosen label", async () => {
    const user = userEvent.setup();
    renderStatefulPicker({
      id: "muted-picker",
      loadPage: async () => ({
        options: [{ value: "1", label: "G-0001 · Nila" }],
        total: 1,
        nextOffset: 1,
      }),
    });

    const placeholder = within(
      screen.getByRole("combobox", { name: "Animal" }),
    ).getByText("Pick an animal");
    expect(placeholder).toHaveClass("truncate", "text-muted-foreground");

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    await user.click(await screen.findByRole("option", { name: /G-0001 · Nila/ }));

    const chosen = within(
      screen.getByRole("combobox", { name: "Animal" }),
    ).getByText("G-0001 · Nila");
    expect(chosen).toHaveClass("truncate");
    expect(chosen).not.toHaveClass("text-muted-foreground");
  });

  it("falls back to the built-in message while no eligible option is checked", async () => {
    const user = userEvent.setup();
    renderPicker({
      id: "default-message-picker",
      loadPage: async () => ({ options: [], total: 100, nextOffset: 50 }),
    });

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(
      await within(dialog).findByText("No eligible options in the records checked yet."),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Load more" })).toBeInTheDocument();
  });

  it("dims the result list and announces the search while a term settles", async () => {
    const user = userEvent.setup();
    let releaseSearch: (() => void) | undefined;
    const searchInFlight = new Promise<void>((resolve) => {
      releaseSearch = resolve;
    });
    const loadPage = vi.fn(async ({ query }: RemotePickerLoadArgs) => {
      if (query) await searchInFlight;
      return { options: [], total: 0, nextOffset: 0 };
    });
    renderPicker({ id: "busy-picker", loadPage });

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    const listbox = within(dialog).getByRole("listbox");
    const search = within(dialog).getByLabelText("Search animals");
    expect(await within(dialog).findByText("No matching options.")).toBeInTheDocument();
    expect(listbox).toHaveClass("max-h-72", "overflow-y-auto");
    expect(listbox).not.toHaveClass("opacity-60");

    await user.type(search, "Nila");

    await waitFor(() => expect(listbox).toHaveClass("opacity-60"));
    expect(within(listbox).getByText("Searching…")).toBeInTheDocument();
    expect(within(listbox).queryByText("No matching options.")).not.toBeInTheDocument();
    expect(search).toHaveAccessibleDescription("Searching…");

    releaseSearch?.();
    await waitFor(() => expect(listbox).not.toHaveClass("opacity-60"));
    expect(within(listbox).getByText("No matching options.")).toBeInTheDocument();
    expect(search).toHaveAccessibleDescription(
      "0 options available. Checked 0 of 0 matching records. All matching records checked.",
    );
  });

  it("counts a single matching record in the singular", async () => {
    const user = userEvent.setup();
    renderPicker({
      id: "singular-picker",
      loadPage: async () => ({
        options: [{ value: "1", label: "G-0001 · Nila" }],
        total: 1,
        nextOffset: 1,
      }),
    });

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(
      await within(dialog).findByText(
        "1 option available. Checked 1 of 1 matching record. All matching records checked.",
      ),
    ).toBeInTheDocument();

    // The selection indicator holds a fixed square so a long label cannot
    // squash it out of the row.
    const option = within(dialog).getByRole("option", { name: /G-0001 · Nila/ });
    expect(option.querySelector(".lucide-check")).toHaveClass("size-4", "shrink-0");
  });

  it("explains that results load on open while the first page is in flight", async () => {
    const user = userEvent.setup();
    let release: (() => void) | undefined;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    renderPicker({
      id: "pending-picker",
      loadPage: async () => {
        await pending;
        return { options: [], total: 0, nextOffset: 0 };
      },
    });

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(within(dialog).getByText("Loading options…")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Search animals")).toHaveAccessibleDescription(
      "Results load when this picker opens.",
    );

    release?.();
    expect(await within(dialog).findByText("No matching options.")).toBeInTheDocument();
  });

  it("ignores ArrowDown from the search box when nothing is listed", async () => {
    const user = userEvent.setup();
    renderPicker({ id: "empty-picker" });

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByText("No matching options.")).toBeInTheDocument();

    const search = within(dialog).getByLabelText("Search animals");
    search.focus();
    // React reports a handler that throws as an uncaught window error rather
    // than failing the dispatch, so watch for one directly.
    const uncaught: unknown[] = [];
    const record = (event: ErrorEvent) => uncaught.push(event.error);
    window.addEventListener("error", record);
    try {
      // The key is still consumed (the dialog must not scroll), but there is
      // no option to hand focus to.
      expect(fireEvent.keyDown(search, { key: "ArrowDown" })).toBe(false);
    } finally {
      window.removeEventListener("error", record);
    }

    expect(uncaught).toEqual([]);
    expect(search).toHaveFocus();
  });
});

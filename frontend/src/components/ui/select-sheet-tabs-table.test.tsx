/**
 * Dedicated tests for four Base UI-backed primitives the pages compose but
 * never test on their own: Select, Sheet, Tabs and Table.
 *
 * What is pinned here is the contract the app leans on — the roles and ARIA
 * wiring screen readers see, the data attributes the styling keys off
 * (`data-size`, `data-side`, `data-variant`, `data-align-trigger`), the
 * variant → class mapping, the keyboard paths, and the value coercion our
 * `Select` wrapper performs on top of Base UI. Class assertions are limited
 * to the utilities that carry those visual states, not the whole string.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectScrollDownButton,
  SelectScrollUpButton,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  tabsListVariants,
} from "@/components/ui/tabs";

// Base UI's popups drive pointer capture, scroll-into-view and a
// ResizeObserver that jsdom does not implement; the page tests install the
// same shims before touching a Select or a Dialog.
beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

const LETTERS = { a: "Alpha", b: "Beta" };

/** The popup Base UI renders around the listbox — our SelectContent element. */
function selectPopup(): HTMLElement {
  return screen.getByRole("listbox").parentElement as HTMLElement;
}

describe("Select", () => {
  it("shows the selected option's label in a labelled combobox trigger", () => {
    render(
      <Select defaultValue="a" items={LETTERS}>
        <SelectTrigger aria-label="Letter">
          <SelectValue placeholder="Pick a letter" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">Alpha</SelectItem>
          <SelectItem value="b">Beta</SelectItem>
        </SelectContent>
      </Select>,
    );

    const trigger = screen.getByRole("combobox", { name: "Letter" });
    expect(trigger).toHaveAttribute("aria-haspopup", "listbox");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    // The label, not the raw value: SelectValue resolves it from `items`.
    const value = trigger.querySelector('[data-slot="select-value"]');
    expect(value).toHaveTextContent("Alpha");
    // Flexes to fill the trigger so the chevron stays pinned to the right.
    expect(value).toHaveClass("flex", "flex-1", "text-left");
    expect(trigger).toHaveClass(
      "flex",
      "w-fit",
      "items-center",
      "justify-between",
      "rounded-lg",
      "border",
    );
    // The closed trigger keeps its chevron affordance.
    expect(trigger.querySelector("svg")).toBeInTheDocument();
  });

  it("renders the placeholder, flagged for the muted-text rule, until a value exists", () => {
    render(
      <Select items={LETTERS}>
        <SelectTrigger aria-label="Letter">
          <SelectValue placeholder="Pick a letter" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">Alpha</SelectItem>
        </SelectContent>
      </Select>,
    );

    const trigger = screen.getByRole("combobox", { name: "Letter" });
    expect(trigger).toHaveTextContent("Pick a letter");
    // data-placeholder is what `data-placeholder:text-muted-foreground` greys.
    expect(trigger).toHaveAttribute("data-placeholder");
  });

  it("advertises its height through data-size, defaulting to the tall trigger", () => {
    const view = render(
      <Select>
        <SelectTrigger aria-label="Letter">
          <SelectValue placeholder="Pick" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">Alpha</SelectItem>
        </SelectContent>
      </Select>,
    );

    // data-[size=default]:h-9 / data-[size=sm]:h-9 hang off this attribute.
    expect(screen.getByRole("combobox")).toHaveAttribute("data-size", "default");

    view.rerender(
      <Select>
        <SelectTrigger aria-label="Letter" size="sm">
          <SelectValue placeholder="Pick" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">Alpha</SelectItem>
        </SelectContent>
      </Select>,
    );

    expect(screen.getByRole("combobox")).toHaveAttribute("data-size", "sm");
  });

  it("lets a caller's className win over the conflicting base utility", () => {
    render(
      <Select>
        <SelectTrigger aria-label="Letter" className="rounded-none w-full">
          <SelectValue placeholder="Pick" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">Alpha</SelectItem>
        </SelectContent>
      </Select>,
    );

    const trigger = screen.getByRole("combobox");
    expect(trigger).toHaveClass("rounded-none", "w-full");
    // tailwind-merge drops the base rounding/width the caller overrode.
    expect(trigger).not.toHaveClass("rounded-lg");
    expect(trigger).not.toHaveClass("w-fit");
  });

  it("opens a listbox of grouped, separated options", async () => {
    const user = userEvent.setup();
    render(
      <Select defaultValue="a" items={LETTERS}>
        <SelectTrigger aria-label="Letter">
          <SelectValue placeholder="Pick" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectLabel>Letters</SelectLabel>
            <SelectItem value="a">Alpha</SelectItem>
            <SelectSeparator />
            <SelectItem value="b">Beta</SelectItem>
          </SelectGroup>
        </SelectContent>
      </Select>,
    );

    await user.click(screen.getByRole("combobox", { name: "Letter" }));

    const listbox = await screen.findByRole("listbox");
    const popup = selectPopup();
    expect(popup).toHaveClass("z-50", "rounded-lg", "bg-popover", "shadow-md");

    // The group is announced by its own label.
    const group = within(listbox).getByRole("group", { name: "Letters" });
    expect(group).toHaveClass("scroll-my-1", "p-1");
    const label = group.querySelector('[data-slot="select-label"]');
    expect(label).toHaveTextContent("Letters");
    expect(label).toHaveClass("text-xs", "text-muted-foreground");
    expect(group).toHaveAttribute("aria-labelledby", label?.id);

    const options = within(listbox).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["Alpha", "Beta"]);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(options[1]).toHaveAttribute("aria-selected", "false");
    expect(options[0]).toHaveClass("rounded-md", "text-sm", "items-center");
    // The tick only accompanies the selected option.
    expect(options[0].querySelector("svg")).toBeInTheDocument();
    expect(options[1].querySelector("svg")).toBeNull();

    const separator = listbox.querySelector('[data-slot="select-separator"]');
    expect(separator).toHaveClass("h-px", "bg-border", "pointer-events-none");
  });

  it("hands the picked value to onValueChange and closes the listbox", async () => {
    const onValueChange = vi.fn();
    const user = userEvent.setup();
    render(
      <Select defaultValue="a" items={LETTERS} onValueChange={onValueChange}>
        <SelectTrigger aria-label="Letter">
          <SelectValue placeholder="Pick" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">Alpha</SelectItem>
          <SelectItem value="b">Beta</SelectItem>
        </SelectContent>
      </Select>,
    );

    await user.click(screen.getByRole("combobox", { name: "Letter" }));
    await user.click(await screen.findByRole("option", { name: "Beta" }));

    expect(onValueChange).toHaveBeenCalledTimes(1);
    expect(onValueChange.mock.calls[0][0]).toBe("b");
    // Second argument is Base UI's event details, forwarded untouched.
    expect(onValueChange.mock.calls[0][1]).toHaveProperty("reason");
    await waitFor(() =>
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("combobox")).toHaveTextContent("Beta");
  });

  it("commits a pick when no onValueChange listener is attached", async () => {
    const user = userEvent.setup();
    render(
      <Select items={LETTERS}>
        <SelectTrigger aria-label="Letter">
          <SelectValue placeholder="Pick a letter" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">Alpha</SelectItem>
          <SelectItem value="b">Beta</SelectItem>
        </SelectContent>
      </Select>,
    );

    await user.click(screen.getByRole("combobox", { name: "Letter" }));
    await user.click(await screen.findByRole("option", { name: "Beta" }));

    // An uncontrolled Select without a listener still records the choice and
    // shuts the popup — the wrapper must not call an absent callback.
    await waitFor(() =>
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
    );
    const trigger = screen.getByRole("combobox", { name: "Letter" });
    expect(trigger).toHaveTextContent("Beta");
    expect(trigger).not.toHaveAttribute("data-placeholder");
  });

  it("reports a cleared selection as an empty string, never null", async () => {
    const seen: unknown[] = [];
    const user = userEvent.setup();

    function Harness() {
      const [letters, setLetters] = useState(["a", "b"]);
      return (
        <>
          <button type="button" onClick={() => setLetters(["b"])}>
            Retire Alpha
          </button>
          <Select defaultValue="a" onValueChange={(value) => seen.push(value)}>
            <SelectTrigger aria-label="Letter">
              <SelectValue placeholder="Pick" />
            </SelectTrigger>
            <SelectContent>
              {letters.map((letter) => (
                <SelectItem key={letter} value={letter}>
                  {letter.toUpperCase()}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </>
      );
    }

    render(<Harness />);
    await user.click(screen.getByRole("combobox", { name: "Letter" }));
    await screen.findByRole("listbox");

    // Base UI drops a selection whose item leaves the list and reports the
    // clear as `null`; callers here are typed for a plain string.
    await user.click(screen.getByRole("button", { name: "Retire Alpha" }));

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen).toEqual([""]);
  });

  it("aligns the popup with the selected item by default", async () => {
    const user = userEvent.setup();
    render(
      <Select defaultValue="a" items={LETTERS}>
        <SelectTrigger aria-label="Letter">
          <SelectValue placeholder="Pick" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">Alpha</SelectItem>
          <SelectItem value="b">Beta</SelectItem>
        </SelectContent>
      </Select>,
    );

    await user.click(screen.getByRole("combobox", { name: "Letter" }));

    await screen.findByRole("listbox");
    // data-[align-trigger=true]:animate-none: an item-aligned popup must not
    // also play the slide-in animation.
    expect(selectPopup()).toHaveAttribute("data-align-trigger", "true");
  });

  it("drops the popup below the trigger unless another side is asked for", async () => {
    const user = userEvent.setup();
    // A trigger-anchored popup (rather than the item-aligned default) is the
    // one whose placement the `side` prop actually steers.
    const anchored = (props: {
      side?: "top" | "bottom";
      align?: "start" | "center";
    }) => (
      <Select defaultValue="a" items={LETTERS}>
        <SelectTrigger aria-label="Letter">
          <SelectValue placeholder="Pick" />
        </SelectTrigger>
        <SelectContent alignItemWithTrigger={false} {...props}>
          <SelectItem value="a">Alpha</SelectItem>
        </SelectContent>
      </Select>
    );

    const view = render(anchored({}));
    await user.click(screen.getByRole("combobox", { name: "Letter" }));
    await screen.findByRole("listbox");
    expect(selectPopup()).toHaveAttribute("data-side", "bottom");
    expect(selectPopup()).toHaveAttribute("data-align", "center");
    expect(selectPopup()).toHaveAttribute("data-align-trigger", "false");
    view.unmount();

    const topView = render(anchored({ side: "top" }));
    await user.click(screen.getByRole("combobox", { name: "Letter" }));
    await screen.findByRole("listbox");
    expect(selectPopup()).toHaveAttribute("data-side", "top");
    topView.unmount();

    // Overriding only the alignment must leave the default side intact —
    // side and align are combined into one placement, so a broken default
    // side surfaces here rather than in the centred case above.
    render(anchored({ align: "start" }));
    await user.click(screen.getByRole("combobox", { name: "Letter" }));
    await screen.findByRole("listbox");
    expect(selectPopup()).toHaveAttribute("data-side", "bottom");
    expect(selectPopup()).toHaveAttribute("data-align", "start");
  });

  it("dismisses the listbox on Escape and hands focus back to the trigger", async () => {
    const user = userEvent.setup();
    render(
      <Select defaultValue="a" items={LETTERS}>
        <SelectTrigger aria-label="Letter">
          <SelectValue placeholder="Pick" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">Alpha</SelectItem>
          <SelectItem value="b">Beta</SelectItem>
        </SelectContent>
      </Select>,
    );

    const trigger = screen.getByRole("combobox", { name: "Letter" });
    await user.click(trigger);
    await screen.findByRole("listbox");

    await user.keyboard("{Escape}");

    await waitFor(() =>
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveFocus();
    // Escape is a dismissal, not a change of mind about the value.
    expect(trigger).toHaveTextContent("Alpha");
  });

  it("pins the scroll arrows to the popup edges, hidden from assistive tech", async () => {
    const user = userEvent.setup();
    render(
      <Select defaultValue="a" items={LETTERS}>
        <SelectTrigger aria-label="Letter">
          <SelectValue placeholder="Pick" />
        </SelectTrigger>
        <SelectContent>
          {/* keepMounted: jsdom reports no overflow, so the arrows would
              otherwise never mount. */}
          <SelectScrollUpButton keepMounted />
          <SelectItem value="a">Alpha</SelectItem>
          <SelectItem value="b">Beta</SelectItem>
          <SelectScrollDownButton keepMounted />
        </SelectContent>
      </Select>,
    );

    await user.click(screen.getByRole("combobox", { name: "Letter" }));
    const listbox = await screen.findByRole("listbox");

    const up = listbox.querySelector<HTMLElement>(
      '[data-slot="select-scroll-up-button"]',
    );
    const down = listbox.querySelector<HTMLElement>(
      '[data-slot="select-scroll-down-button"]',
    );
    expect(up).toHaveClass("top-0", "z-10", "w-full", "bg-popover");
    expect(down).toHaveClass("bottom-0", "z-10", "w-full", "bg-popover");
    // Pointer affordances only — the options themselves stay reachable.
    expect(up).toHaveAttribute("aria-hidden", "true");
    expect(down).toHaveAttribute("aria-hidden", "true");
    expect(up?.querySelector("svg")).toHaveClass("lucide-chevron-up");
    expect(down?.querySelector("svg")).toHaveClass("lucide-chevron-down");
  });
});

describe("Sheet", () => {
  function renderSheet(props: {
    side?: "top" | "right" | "bottom" | "left";
    showCloseButton?: boolean;
  } = {}) {
    return render(
      <Sheet>
        <SheetTrigger>Open filters</SheetTrigger>
        <SheetContent {...props}>
          <SheetHeader>
            <SheetTitle>Filters</SheetTitle>
            <SheetDescription>Narrow the herd list.</SheetDescription>
          </SheetHeader>
          <SheetFooter>
            <SheetClose>Done</SheetClose>
          </SheetFooter>
        </SheetContent>
      </Sheet>,
    );
  }

  it("opens a described dialog panel over a dimming backdrop", async () => {
    const user = userEvent.setup();
    renderSheet();

    const trigger = screen.getByRole("button", { name: "Open filters" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(trigger);

    const dialog = await screen.findByRole("dialog", { name: "Filters" });
    expect(dialog).toHaveAccessibleDescription("Narrow the herd list.");
    expect(dialog).toHaveClass("fixed", "z-50", "flex", "flex-col", "bg-popover");

    const title = dialog.querySelector('[data-slot="sheet-title"]');
    expect(title?.tagName).toBe("H2");
    expect(title).toHaveClass("font-heading", "text-base", "font-medium");
    expect(dialog.querySelector('[data-slot="sheet-description"]')).toHaveClass(
      "text-sm",
      "text-muted-foreground",
    );
    expect(dialog.querySelector('[data-slot="sheet-header"]')).toHaveClass(
      "flex",
      "flex-col",
      "p-4",
    );
    // mt-auto keeps the footer pinned to the bottom of a tall panel.
    expect(dialog.querySelector('[data-slot="sheet-footer"]')).toHaveClass(
      "mt-auto",
      "flex",
      "flex-col",
      "p-4",
    );

    const overlay = document.querySelector('[data-slot="sheet-overlay"]');
    expect(overlay).toHaveClass("fixed", "inset-0", "z-50", "bg-black/10");
  });

  it("slides in from the right unless another side is asked for", async () => {
    const user = userEvent.setup();
    const view = renderSheet();

    await user.click(screen.getByRole("button", { name: "Open filters" }));
    // data-[side=right]:right-0 and its slide-in transform key off this.
    expect(await screen.findByRole("dialog")).toHaveAttribute("data-side", "right");

    view.unmount();
    renderSheet({ side: "left" });
    await user.click(screen.getByRole("button", { name: "Open filters" }));

    expect(await screen.findByRole("dialog")).toHaveAttribute("data-side", "left");
  });

  it("offers a labelled close button that shuts the panel", async () => {
    const user = userEvent.setup();
    renderSheet();
    await user.click(screen.getByRole("button", { name: "Open filters" }));
    const dialog = await screen.findByRole("dialog");

    const close = within(dialog).getByRole("button", { name: "Close" });
    // Ghost icon button parked in the panel's top corner.
    expect(close).toHaveClass("absolute", "top-3", "right-3");
    await user.click(close);

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("omits the built-in close button when the caller supplies its own", async () => {
    const user = userEvent.setup();
    renderSheet({ showCloseButton: false });

    await user.click(screen.getByRole("button", { name: "Open filters" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).queryByRole("button", { name: "Close" })).toBeNull();
    // The composed SheetClose in the footer is still the way out.
    expect(within(dialog).getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  it("closes from a composed SheetClose and from Escape", async () => {
    const user = userEvent.setup();
    renderSheet();
    await user.click(screen.getByRole("button", { name: "Open filters" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "Open filters" }));
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});

describe("Tabs", () => {
  function renderTabs(props: { variant?: "default" | "line" } = {}) {
    return render(
      <Tabs defaultValue="active">
        <TabsList variant={props.variant}>
          <TabsTrigger value="active">Active</TabsTrigger>
          <TabsTrigger value="sold">Sold</TabsTrigger>
          <TabsTrigger value="dead" disabled>
            Dead
          </TabsTrigger>
        </TabsList>
        <TabsContent value="active">Active herd</TabsContent>
        <TabsContent value="sold">Sold animals</TabsContent>
        <TabsContent value="dead">Dead animals</TabsContent>
      </Tabs>,
    );
  }

  it("wires each tab to the panel it controls and shows only the active one", () => {
    renderTabs();

    const tablist = screen.getByRole("tablist");
    const [active, sold] = within(tablist).getAllByRole("tab");
    expect(active).toHaveAttribute("aria-selected", "true");
    expect(sold).toHaveAttribute("aria-selected", "false");

    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("Active herd");
    expect(panel).toHaveAttribute("aria-labelledby", active.id);
    expect(active).toHaveAttribute("aria-controls", panel.id);
    expect(screen.queryByText("Sold animals")).not.toBeInTheDocument();
    expect(panel).toHaveClass("flex-1", "text-sm", "outline-none");
  });

  it("swaps the panel on click and walks the tabs with the arrow keys", async () => {
    const user = userEvent.setup();
    renderTabs();

    await user.click(screen.getByRole("tab", { name: "Sold" }));

    expect(screen.getByRole("tabpanel")).toHaveTextContent("Sold animals");
    expect(screen.getByRole("tab", { name: "Sold" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    // The tablist is a single tab stop; the arrows move within it.
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Active" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Active herd");
  });

  it("keeps a disabled tab inert", async () => {
    const user = userEvent.setup();
    renderTabs();

    const dead = screen.getByRole("tab", { name: "Dead" });
    expect(dead).toHaveAttribute("aria-disabled", "true");
    // aria-disabled:pointer-events-none / aria-disabled:opacity-50 grey it out.
    expect(dead).toHaveClass(
      "aria-disabled:pointer-events-none",
      "aria-disabled:opacity-50",
    );

    await user.click(dead);

    expect(screen.getByRole("tabpanel")).toHaveTextContent("Active herd");
  });

  it("lays the tabs out horizontally unless a vertical orientation is asked for", () => {
    const { container } = render(
      <Tabs defaultValue="active">
        <TabsList>
          <TabsTrigger value="active">Active</TabsTrigger>
        </TabsList>
      </Tabs>,
    );

    const root = container.querySelector('[data-slot="tabs"]');
    expect(root).toHaveAttribute("data-orientation", "horizontal");
    expect(root).toHaveClass("group/tabs", "flex", "gap-2", "data-horizontal:flex-col");

    const vertical = render(
      <Tabs defaultValue="active" orientation="vertical">
        <TabsList>
          <TabsTrigger value="active">Active</TabsTrigger>
        </TabsList>
      </Tabs>,
    );

    expect(
      vertical.container.querySelector('[data-slot="tabs"]'),
    ).toHaveAttribute("data-orientation", "vertical");
  });

  it("styles the list as a muted pill by default and a bare row for the line variant", () => {
    const view = renderTabs();

    const list = screen.getByRole("tablist");
    expect(list).toHaveAttribute("data-variant", "default");
    expect(list).toHaveClass("inline-flex", "w-fit", "rounded-lg", "bg-muted");

    view.unmount();
    renderTabs({ variant: "line" });

    const lineList = screen.getByRole("tablist");
    // data-variant is what the trigger's group-data-[variant=line] rules read.
    expect(lineList).toHaveAttribute("data-variant", "line");
    expect(lineList).toHaveClass("gap-1", "bg-transparent", "inline-flex");
    expect(lineList).not.toHaveClass("bg-muted");
  });

  it("carries the active, line and underline rules on every trigger", () => {
    renderTabs();

    const trigger = screen.getByRole("tab", { name: "Active" });
    expect(trigger).toHaveAttribute("data-active");
    // Base pill styling…
    expect(trigger).toHaveClass("relative", "inline-flex", "text-muted-foreground");
    // …the line variant's transparent overrides…
    expect(trigger).toHaveClass(
      "group-data-[variant=line]/tabs-list:bg-transparent",
    );
    // …the active pill fill…
    expect(trigger).toHaveClass("data-active:bg-background", "data-active:text-foreground");
    // …and the underline the line variant reveals when active.
    expect(trigger).toHaveClass(
      "after:absolute",
      "after:bg-foreground",
      "after:opacity-0",
      "group-data-[variant=line]/tabs-list:data-active:after:opacity-100",
    );
  });

  it("resolves tabsListVariants to the muted pill when called bare", () => {
    // The variants map is exported for callers that style their own list.
    expect(tabsListVariants()).toBe(tabsListVariants({ variant: "default" }));
    expect(tabsListVariants()).toContain("bg-muted");
    expect(tabsListVariants()).toContain("inline-flex");
    expect(tabsListVariants({ variant: "line" })).toContain("gap-1 bg-transparent");
    expect(tabsListVariants({ variant: "line" })).not.toContain("bg-muted");
  });
});

describe("Table", () => {
  function renderTable() {
    return render(
      <Table>
        <TableCaption>Herd summary</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead>Tag</TableHead>
            <TableHead>Weight</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow data-state="selected">
            <TableCell>G-001</TableCell>
            <TableCell>32.5</TableCell>
          </TableRow>
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell>Total</TableCell>
            <TableCell>32.5</TableCell>
          </TableRow>
        </TableFooter>
      </Table>,
    );
  }

  it("renders a semantic table inside its own horizontal scroll container", () => {
    const { container } = renderTable();

    const scroller = container.querySelector('[data-slot="table-container"]');
    // Wide herd tables scroll inside the container, never the page body.
    expect(scroller).toHaveClass("relative", "w-full", "overflow-x-auto");

    const table = screen.getByRole("table");
    expect(scroller).toContainElement(table);
    expect(table.tagName).toBe("TABLE");
    // caption-bottom puts the caption under the rows it describes.
    expect(table).toHaveClass("w-full", "caption-bottom", "text-sm");

    const caption = table.querySelector("caption");
    expect(caption).toHaveTextContent("Herd summary");
    expect(caption).toHaveClass("mt-4", "text-sm", "text-muted-foreground");
  });

  it("keeps the header, body and footer in their own row groups", () => {
    const { container } = renderTable();

    const head = container.querySelector('[data-slot="table-header"]');
    const body = container.querySelector('[data-slot="table-body"]');
    const foot = container.querySelector('[data-slot="table-footer"]');
    expect(head?.tagName).toBe("THEAD");
    expect(body?.tagName).toBe("TBODY");
    expect(foot?.tagName).toBe("TFOOT");
    // Rules under each header row, none under the last body row.
    expect(head).toHaveClass("[&_tr]:border-b");
    expect(body).toHaveClass("[&_tr:last-child]:border-0");
    expect(foot).toHaveClass("border-t", "bg-muted/50", "font-medium");

    const columnHeaders = screen.getAllByRole("columnheader");
    expect(columnHeaders.map((cell) => cell.textContent)).toEqual(["Tag", "Weight"]);
    expect(columnHeaders[0].tagName).toBe("TH");
    expect(columnHeaders[0]).toHaveClass("h-11", "px-2", "text-left", "font-medium");

    const bodyCells = within(body as HTMLElement).getAllByRole("cell");
    expect(bodyCells.map((cell) => cell.textContent)).toEqual(["G-001", "32.5"]);
    expect(bodyCells[0].tagName).toBe("TD");
    expect(bodyCells[0]).toHaveClass("p-2", "align-middle", "whitespace-nowrap");
  });

  it("marks a selected row and keeps the hover/expanded highlight rules", () => {
    const { container } = renderTable();

    const selected = container.querySelector('[data-state="selected"]');
    expect(selected?.tagName).toBe("TR");
    expect(selected).toHaveClass(
      "border-b",
      "hover:bg-muted/50",
      "has-aria-expanded:bg-muted/50",
      "data-[state=selected]:bg-muted",
    );
  });

  it("lets a caller's padding override the cell default", () => {
    render(
      <Table>
        <TableBody>
          <TableRow className="cursor-pointer">
            <TableCell className="p-4">G-002</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );

    const cell = screen.getByRole("cell");
    expect(cell).toHaveClass("p-4", "align-middle");
    expect(cell).not.toHaveClass("p-2");
    expect(screen.getByRole("row")).toHaveClass("cursor-pointer", "border-b");
  });
});

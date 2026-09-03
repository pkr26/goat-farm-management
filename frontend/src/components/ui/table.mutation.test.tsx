/**
 * Mutation-hardening for src/components/ui/table.tsx.
 *
 * The table primitives are pure markup: the contract is the element structure
 * (thead/tbody/tfoot/tr/th/td/caption under the scroll container, each with
 * its data-slot that the stylesheet's data-slot selectors key on) and the
 * SortableTableHead wiring — aria-sort per direction, the matching lucide
 * glyph, and the onSort callback with the caller's column key.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  SortableTableHead,
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
  type SortDirection,
} from "@/components/ui/table";

function renderOneSort(
  direction: SortDirection | null | undefined,
  onSort = () => undefined,
) {
  const { container } = render(
    <table>
      <thead>
        <tr>
          <SortableTableHead
            column="tag"
            label="Tag"
            direction={direction}
            onSort={onSort}
          />
        </tr>
      </thead>
    </table>,
  );
  const head = container.querySelector("th");
  if (!head) throw new Error("no th rendered");
  return { head, button: head.querySelector("button") };
}

describe("table markup primitives", () => {
  it("nests a captioned, sectioned table in its scroll container", () => {
    const { container } = render(
      <Table>
        <TableCaption>Herd inventory</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">Tag</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>G-001</TableCell>
          </TableRow>
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell>1 head</TableCell>
          </TableRow>
        </TableFooter>
      </Table>,
    );

    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.tagName).toBe("DIV");
    expect(wrapper).toHaveAttribute("data-slot", "table-container");
    expect(wrapper).toHaveClass("relative", "w-full", "overflow-x-auto");

    const table = wrapper.querySelector("table");
    expect(table).toHaveAttribute("data-slot", "table");
    expect(table).toHaveClass("table-numeric", "w-full", "caption-bottom", "text-sm");

    expect(table?.querySelector("caption")).toHaveAttribute(
      "data-slot",
      "table-caption",
    );
    expect(table?.querySelector("caption")).toHaveTextContent("Herd inventory");

    const header = table?.querySelector("thead");
    expect(header).toHaveAttribute("data-slot", "table-header");
    expect(header?.querySelectorAll("tr")).toHaveLength(1);

    const body = table?.querySelector("tbody");
    expect(body).toHaveAttribute("data-slot", "table-body");
    expect(body?.querySelectorAll("tr")).toHaveLength(1);

    const footer = table?.querySelector("tfoot");
    expect(footer).toHaveAttribute("data-slot", "table-footer");

    expect(table?.querySelectorAll("tr")).toHaveLength(3);
    expect(table?.querySelector("tr")).toHaveAttribute("data-slot", "table-row");
    expect(table?.querySelector("th")).toHaveAttribute("data-slot", "table-head");
    expect(table?.querySelector("th")).toHaveAttribute("scope", "col");
    expect(table?.querySelector("td")).toHaveAttribute("data-slot", "table-cell");
    expect(table).toHaveTextContent("G-001");
  });

  it("merges caller classes onto every layer without dropping the base", () => {
    const { container } = render(
      <Table className="table-fixed">
        <TableHeader className="sticky-header">
          <TableRow className="cursor-pointer">
            <TableHead className="w-10">A</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody className="divide-y">
          <TableRow>
            <TableCell className="font-medium">B</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );

    const table = container.querySelector("table");
    expect(table).toHaveClass("table-fixed", "table-numeric");
    expect(table?.querySelector("thead")).toHaveClass("sticky-header");
    expect(table?.querySelector("tr")).toHaveClass("cursor-pointer", "border-b");
    expect(table?.querySelector("th")).toHaveClass("w-10", "h-11", "text-left");
    expect(table?.querySelector("td")).toHaveClass("font-medium", "p-2");
  });
});

describe("SortableTableHead", () => {
  it.each([
    { direction: "asc" as const, ariaSort: "ascending", icon: "lucide-arrow-up" },
    { direction: "desc" as const, ariaSort: "descending", icon: "lucide-arrow-down" },
    {
      direction: undefined,
      ariaSort: "none",
      icon: "lucide-arrow-down-up",
    },
    { direction: null, ariaSort: "none", icon: "lucide-arrow-down-up" },
  ])(
    "reports $ariaSort with the $icon glyph for direction $direction",
    ({ direction, ariaSort, icon }) => {
      const { head, button } = renderOneSort(direction);

      expect(head).toHaveAttribute("aria-sort", ariaSort);
      expect(button).not.toBeNull();
      expect(button).toHaveAttribute("type", "button");
      expect(button).toHaveTextContent("Tag");
      expect(button?.querySelector("svg")).toHaveClass(icon);
      expect(button?.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    },
  );

  it("publishes the focus-ring styles on the cell and the button chrome", () => {
    const { head, button } = renderOneSort("asc");

    expect(head.className).toContain("[&>button]:focus-visible:ring-[3px]");
    expect(head.className).toContain("[&>button]:focus-visible:outline-none");
    expect(button).toHaveClass(
      "inline-flex",
      "h-9",
      "items-center",
      "gap-1",
      "text-left",
    );
  });

  it("forwards the caller's column key from the button click", async () => {
    const onSort = vi.fn();
    const { button } = renderOneSort(undefined, onSort);

    await userEvent.setup().click(button!);
    expect(onSort).toHaveBeenCalledExactlyOnceWith("tag");
  });

  it("keeps per-column state independent when several heads sort", () => {
    const onSortTag = vi.fn();
    const onSortBorn = vi.fn();
    const { container } = render(
      <table>
        <thead>
          <tr>
            <SortableTableHead
              column="tag"
              label="Tag"
              direction="asc"
              onSort={onSortTag}
            />
            <SortableTableHead
              column="born-on"
              label="Born on"
              direction={null}
              onSort={onSortBorn}
            />
          </tr>
        </thead>
      </table>,
    );

    const heads = container.querySelectorAll("th");
    expect(heads[0]).toHaveAttribute("aria-sort", "ascending");
    expect(heads[1]).toHaveAttribute("aria-sort", "none");
    expect(heads[0].querySelector("svg")?.getAttribute("class")).toContain(
      "lucide-arrow-up",
    );
    expect(heads[1].querySelector("svg")?.getAttribute("class")).toContain(
      "lucide-arrow-down-up",
    );

    const bornButton = screen.getByRole("button", { name: "Born on" });
    bornButton.click();
    expect(onSortTag).not.toHaveBeenCalled();
    expect(onSortBorn).toHaveBeenCalledWith("born-on");
  });
});

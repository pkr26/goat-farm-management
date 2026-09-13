/**
 * Forms layer — fresh-domain mutation campaign kills (2026-09): the remote
 * picker's closed-state contract and id wiring, single-debounce search, the
 * health animal picker's movement-restriction label, and
 * PaginationControls' sanitisation of hostile limit/offset values.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { HttpResponse, http } from "msw";

import { HealthAnimalPicker } from "@/components/health-target-pickers";
import { PaginationControls, sanitizeOffset } from "@/components/pagination-controls";
import {
  RemotePicker,
  type RemotePickerLoadArgs,
  type RemotePickerPage,
} from "@/components/remote-picker";
import { Label } from "@/components/ui/label";
import { server } from "@/test/msw-server";
import { createTestQueryClient } from "@/test/render";

function pickerHarness({
  loadPage,
  disabled = false,
}: {
  loadPage: (args: RemotePickerLoadArgs) => Promise<RemotePickerPage>;
  disabled?: boolean;
}) {
  function Harness() {
    return (
      <QueryClientProvider client={createTestQueryClient()}>
        <Label htmlFor="animal-picker">Animal</Label>
        <RemotePicker
          id="animal-picker"
          value=""
          onValueChange={() => undefined}
          selectedOption={null}
          placeholder="Pick an animal"
          dialogTitle="Choose an animal"
          searchLabel="Search animals"
          disabled={disabled}
          sourcePath="/api/animals"
          cacheKey={["campaign"]}
          loadPage={loadPage}
        />
      </QueryClientProvider>
    );
  }
  return Harness;
}

const firstPage = async (): Promise<RemotePickerPage> => ({
  options: [
    { value: "1", label: "G-001 · Lakshmi" },
    { value: "2", label: "G-002 · Kaveri" },
  ],
  total: 2,
  nextOffset: null as unknown as number,
});

describe("RemotePicker — campaign kills", () => {
  it("renders no dialog and wires aria ids while closed", async () => {
    const Harness = pickerHarness({ loadPage: firstPage });
    render(<Harness />);

    const trigger = screen.getByRole("combobox", { name: "Animal" });
    // Closed state: no dialog content in the DOM, and the trigger controls
    // the dialog id derived from the picker id.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-controls", "animal-picker-picker-dialog");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("debounces a burst of typing into a single search read", async () => {
    const loadPage = vi.fn(
      async ({ query }: RemotePickerLoadArgs): Promise<RemotePickerPage> => ({
        options: query ? [{ value: "9", label: `G-009 · ${query}` }] : [],
        total: query ? 1 : 2,
        nextOffset: null as unknown as number,
      }),
    );
    const Harness = pickerHarness({ loadPage });
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const search = await screen.findByLabelText("Search animals");
    await user.type(search, "G-0");
    // Only the settled query string may hit the loader (plus the initial
    // empty page): a stale-handle cleanup bug double-fires the debounce.
    await waitFor(() => expect(loadPage).toHaveBeenCalledWith(expect.objectContaining({ query: "G-0" })));
    const searchCalls = loadPage.mock.calls.filter(
      (call) => (call[0] as RemotePickerLoadArgs).query === "G-0",
    );
    expect(searchCalls).toHaveLength(1);
  });
});

describe("HealthAnimalPicker — campaign kills", () => {
  it("surfaces a movement restriction in the option label", async () => {
    function Harness() {
      return (
        <QueryClientProvider client={createTestQueryClient()}>
          <Label htmlFor="hp">Animal</Label>
          <HealthAnimalPicker
            id="hp"
            value=""
            onValueChange={() => undefined}
            placeholder="Pick an animal"
          />
        </QueryClientProvider>
      );
    }
    server.use(
      http.get("/api/health/animals", () =>
        HttpResponse.json({
          animals: [
            {
              id: 3,
              tag_number: "G-003",
              name: "Kaveri",
              current_bucket: "BREEDING",
              movement_restricted: true,
              restriction_version: 2,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
    );
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    expect(
      await screen.findByText(/G-003 · Kaveri — BREEDING — movement restricted/),
    ).toBeInTheDocument();
  });
});

describe("sanitizeOffset — campaign kills", () => {
  it("normalizes hostile offsets to whole non-negative values", () => {
    expect(sanitizeOffset(Number.NaN)).toBe(0);
    expect(sanitizeOffset(-3)).toBe(0);
    expect(sanitizeOffset(2.7)).toBe(2);
    expect(sanitizeOffset(0)).toBe(0);
    expect(sanitizeOffset(50)).toBe(50);
  });
});

describe("PaginationControls — campaign kills", () => {
  const page = (props: Record<string, unknown>) => (
    <PaginationControls
      total={5}
      limit={10}
      offset={0}
      onOffsetChange={() => undefined}
      label="items"
      {...props}
    />
  );

  it("sanitizes hostile limit values to one", () => {
    const { rerender, container } = render(page({}));
    expect(container).toHaveTextContent("Showing 1–5 of 5 items");

    // Non-positive limits fall back to one…
    for (const limit of [0, -3]) {
      rerender(page({ limit }));
      expect(container).toHaveTextContent("Showing 1–1 of 5 items");
    }
    // …and a fractional limit truncates to whole items, never "1–4.5".
    rerender(page({ limit: 4.5 }));
    expect(container).toHaveTextContent("Showing 1–4 of 5 items");
  });

  it("sanitizes hostile offset values to zero", () => {
    const { rerender, container } = render(page({ offset: Number.NaN }));
    expect(container).toHaveTextContent("Showing 1–5 of 5 items");
    rerender(page({ offset: -3 }));
    expect(container).toHaveTextContent("Showing 1–5 of 5 items");
    // Fractional offsets truncate to whole items.
    rerender(page({ offset: 2.7 }));
    expect(container).toHaveTextContent("Showing 3–5 of 5 items");
  });

  it("hides entirely for empty lists", () => {
    const { container } = render(page({ total: 0 }));
    expect(container).toBeEmptyDOMElement();
  });
});


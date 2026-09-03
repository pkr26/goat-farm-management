/**
 * Mutation-hardening for src/components/remote-picker.tsx.
 *
 * Pins three behaviours the flows in remote-picker.test.tsx pass through too
 * quickly to pin exactly: the query cache must be scoped by sourcePath (two
 * pickers over the same cacheKey but different endpoints may never see each
 * other's pages), ArrowDown must wrap from the last option back to the first,
 * and the Load-more handler must not stack a second page fetch while one is
 * already in flight even when the disabled attribute is bypassed.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function StatefulPicker({
  id,
  label,
  sourcePath,
  cacheKey,
  loadPage,
}: {
  id: string;
  label: string;
  sourcePath: string;
  cacheKey: readonly unknown[];
  loadPage: (args: RemotePickerLoadArgs) => Promise<RemotePickerPage>;
}) {
  const [value, setValue] = useState("");
  return (
    <RemotePicker
      id={id}
      value={value}
      onValueChange={setValue}
      placeholder={`Pick ${label}`}
      dialogTitle={`Choose ${label}`}
      searchLabel={`Search ${label}`}
      sourcePath={sourcePath}
      cacheKey={cacheKey}
      loadPage={loadPage}
    />
  );
}

describe("RemotePicker cache scoping", () => {
  it("never serves one source's cached page to a picker on another source", async () => {
    const user = userEvent.setup();
    const client = createTestQueryClient();
    const loadAlpha = vi.fn(async () => ({
      options: [{ value: "a1", label: "Alpha one" }],
      total: 1,
      nextOffset: 1,
    }));
    // Beta never resolves: whatever renders in its dialog must be its own
    // pending state, never Alpha's cached options.
    const loadBeta = vi.fn(
      () => new Promise<RemotePickerPage>(() => undefined),
    );

    render(
      <QueryClientProvider client={client}>
        <Label htmlFor="alpha-picker">Alpha source</Label>
        <StatefulPicker
          id="alpha-picker"
          label="alpha"
          sourcePath="/api/alpha"
          cacheKey={["shared"]}
          loadPage={loadAlpha}
        />
        <Label htmlFor="beta-picker">Beta source</Label>
        <StatefulPicker
          id="beta-picker"
          label="beta"
          sourcePath="/api/beta"
          cacheKey={["shared"]}
          loadPage={loadBeta}
        />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("combobox", { name: "Alpha source" }));
    const alphaDialog = screen.getByRole("dialog", { name: "Choose alpha" });
    expect(
      await within(alphaDialog).findByRole("option", { name: /Alpha one/ }),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Choose alpha" })).not.toBeInTheDocument(),
    );

    await user.click(screen.getByRole("combobox", { name: "Beta source" }));
    const betaDialog = screen.getByRole("dialog", { name: "Choose beta" });
    expect(await within(betaDialog).findByText("Loading options…")).toBeInTheDocument();
    expect(within(betaDialog).queryByRole("option")).not.toBeInTheDocument();
    expect(screen.queryByText(/Alpha one/)).not.toBeInTheDocument();
    expect(loadBeta).toHaveBeenCalledOnce();
  });
});

describe("RemotePicker option roving focus", () => {
  async function renderThreeOptions() {
    const user = userEvent.setup();
    const loadPage = vi.fn(async () => ({
      options: [
        { value: "1", label: "G-0001 · Nila" },
        { value: "2", label: "G-0002 · Tara" },
        { value: "3", label: "G-0003 · Mira" },
      ],
      total: 3,
      nextOffset: 3,
    }));
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <Label htmlFor="wrap-picker">Animal</Label>
        <StatefulPicker
          id="wrap-picker"
          label="animal"
          sourcePath="/api/animals"
          cacheKey={["wrap"]}
          loadPage={loadPage}
        />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    return {
      user,
      first: await screen.findByRole("option", { name: /G-0001 · Nila/ }),
      last: screen.getByRole("option", { name: /G-0003 · Mira/ }),
    };
  }

  it("wraps ArrowDown focus from the last option back to the first", async () => {
    const { user, first, last } = await renderThreeOptions();

    last.focus();
    expect(last).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(first).toHaveFocus();
  });

  it("keeps ArrowDown from the search box landing on the first option", async () => {
    const { user, first } = await renderThreeOptions();

    const search = screen.getByLabelText("Search animal");
    search.focus();
    await user.keyboard("{ArrowDown}");
    expect(first).toHaveFocus();
  });
});

describe("RemotePicker load-more guard", () => {
  it("ignores an extra activation while the next page is still fetching", async () => {
    const user = userEvent.setup();
    let releaseNextPage: (() => void) | undefined;
    const nextPagePending = new Promise<void>((resolve) => {
      releaseNextPage = resolve;
    });
    const loadPage = vi.fn(async ({ offset }: RemotePickerLoadArgs) => {
      if (offset > 0) await nextPagePending;
      return offset === 0
        ? { options: [], total: 60, nextOffset: 50 }
        : {
            options: [{ value: "51", label: "G-0051 · Reached" }],
            total: 60,
            nextOffset: 60,
          };
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <Label htmlFor="guard-picker">Animal</Label>
        <StatefulPicker
          id="guard-picker"
          label="animal"
          sourcePath="/api/animals"
          cacheKey={["guard"]}
          loadPage={loadPage}
        />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose animal" });
    expect(await within(dialog).findByRole("button", { name: "Load more" })).toBeEnabled();

    await user.click(within(dialog).getByRole("button", { name: "Load more" }));
    const busy = await within(dialog).findByRole("button", { name: "Loading more…" });
    expect(busy).toBeDisabled();

    // fireEvent bypasses the disabled attribute; the handler must still refuse.
    fireEvent.click(busy);
    fireEvent.click(busy);

    releaseNextPage?.();
    expect(
      await within(dialog).findByRole("option", { name: /G-0051 · Reached/ }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(dialog).getByText(/All matching records checked/),
      ).toBeInTheDocument(),
    );
    expect(loadPage).toHaveBeenCalledTimes(2);
    expect(loadPage.mock.calls.filter(([args]) => args.offset === 50)).toHaveLength(1);
  });
});

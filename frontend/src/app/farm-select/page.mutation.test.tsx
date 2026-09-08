/**
 * Farm-select page — mutation-hardening suite for the surviving branches:
 * the create-farm happy path (trimmed payload, form reset, navigation),
 * the empty-list hint, and the per-card type labels and timezone
 * fallbacks on the picker grid.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FarmSelectPage from "./page";

const { pushMock, replaceMock, navState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  navState: { search: "" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/farm-select",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

const GOAT_FARM = {
  id: 1,
  name: "Test Goat Farm",
  location: "Solapur",
  role: null,
  timezone: "Asia/Kolkata",
};

const SECOND_FARM = {
  id: 2,
  name: "Navipet Herd",
  location: null,
  role: "Mover",
  timezone: "Asia/Kolkata",
};

async function renderLoaded(expectedCards: number) {
  renderWithProviders(<FarmSelectPage />);
  await waitFor(() =>
    expect(screen.getAllByRole("button").filter((b) => b.closest(".grid"))).toHaveLength(
      expectedCards,
    ),
  );
}

function cardOf(name: string): HTMLElement {
  const card = screen.getByText(name).closest("button");
  expect(card).not.toBeNull();
  return card as HTMLElement;
}

describe("FarmSelectPage — create farm", () => {
  let createBodies: Array<Record<string, unknown>>;

  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    navState.search = "";
    createBodies = [];
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json([GOAT_FARM, SECOND_FARM])),
      http.post("/api/auth/farms", async ({ request }) => {
        createBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          { status: 201 },
        );
      }),
    );
  });

  it("posts the trimmed draft, resets the form and opens the new farm", async () => {
    const user = userEvent.setup();
    await renderLoaded(2);

    const name = screen.getByLabelText("Farm name");
    await user.type(name, "  New Farm  ");
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    await waitFor(() => expect(createBodies).toHaveLength(1));
    expect(createBodies[0]).toEqual({
      name: "New Farm",
      location: null,
      timezone: "Asia/Kolkata",
    });
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    // The draft is reset after the durable create, ready for another farm.
    await waitFor(() => expect(name).toHaveValue(""));
  });


  it("resets the whole draft when opening the fresh farm fails", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "farm bootstrap failed" }, { status: 503 }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded(2);

    const name = screen.getByLabelText("Farm name");
    await user.type(name, "New Farm");
    await user.type(screen.getByLabelText(/Location/), "Somewhere");
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    expect(await screen.findByText("farm bootstrap failed")).toBeInTheDocument();
    // The durable create still reset the draft to the pristine defaults.
    await waitFor(() => expect(name).toHaveValue(""));
    expect(screen.getByLabelText(/Location/)).toHaveValue("");
    expect(screen.getByLabelText(/Farm timezone/)).toHaveValue("Asia/Kolkata");
  });
});

describe("FarmSelectPage — picker cards", () => {
  beforeEach(() => {
    pushMock.mockClear();
    navState.search = "";
  });

  it("labels each card with its type and timezone", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([GOAT_FARM, SECOND_FARM])));
    await renderLoaded(2);

    const goat = cardOf("Test Goat Farm");
    expect(within(goat).getByText("Goat farm")).toBeInTheDocument();
    expect(within(goat).getByText("Asia/Kolkata")).toBeInTheDocument();
    expect(within(goat).getByText("current")).toBeInTheDocument();
    // Species icon: the goat herd shows the paw print.
    expect(goat.querySelector("svg.lucide-paw-print")).not.toBeNull();
    // The type label and timezone are separated by a real " · ".
    const footer = goat.querySelector("p.text-xs") as HTMLElement;
    expect(footer.textContent).toBe("Goat farm · Asia/Kolkata");

    // Timezone is required on the generated FarmOut contract, so every
    // card renders its farm's own zone.
    const second = cardOf("Navipet Herd");
    expect(within(second).getByText("Goat farm")).toBeInTheDocument();
    expect(within(second).getByText("Asia/Kolkata")).toBeInTheDocument();
    expect(within(second).queryByText("current")).not.toBeInTheDocument();
  });

  it("shows the no-farms hint instead of an empty grid", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));
    renderWithProviders(<FarmSelectPage />);

    expect(
      await screen.findByText("No farms yet — create your first one below."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Test Goat Farm")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Test Goat Farm/ })).not.toBeInTheDocument();
  });
});

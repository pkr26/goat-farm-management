/**
 * Farm-select page — mutation-hardening suite for the surviving branches:
 * the create-farm happy path (trimmed payload, form reset, navigation),
 * the empty-list hint, and the per-card farm-type labels and timezone
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
  farm_type: "GOAT",
  timezone: "Asia/Kolkata",
};

const DAIRY_FARM = {
  id: 2,
  name: "Navipet Dairy",
  location: null,
  role: "Mover",
  farm_type: "BUFFALO_DAIRY",
  // No timezone: the card must fall back to Asia/Kolkata.
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
      http.get("/api/auth/farms", () => HttpResponse.json([GOAT_FARM, DAIRY_FARM])),
      http.post("/api/auth/farms", async ({ request }) => {
        createBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          { id: 9, name: "New Farm", location: null, role: null, farm_type: "GOAT", timezone: "Asia/Kolkata" },
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
      farm_type: "GOAT",
      location: null,
      timezone: "Asia/Kolkata",
    });
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    // The draft is reset after the durable create, ready for another farm.
    await waitFor(() => expect(name).toHaveValue(""));
  });

  it("sends an explicit location and a dairy farm type through", async () => {
    const user = userEvent.setup();
    await renderLoaded(2);

    await user.type(screen.getByLabelText("Farm name"), "Dairy #2");
    await user.type(screen.getByLabelText(/Location/), "Navipet");
    await user.click(screen.getByRole("radiogroup", { name: "Farm type" }).querySelector('input[value="BUFFALO_DAIRY"]')!);
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    await waitFor(() => expect(createBodies).toHaveLength(1));
    expect(createBodies[0]).toEqual({
      name: "Dairy #2",
      farm_type: "BUFFALO_DAIRY",
      location: "Navipet",
      timezone: "Asia/Kolkata",
    });
  });

  it("resets the whole draft — including the goat radio — when opening the fresh farm fails", async () => {
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
    await user.click(screen.getByRole("radiogroup", { name: "Farm type" }).querySelector('input[value="BUFFALO_DAIRY"]')!);
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    expect(await screen.findByText("farm bootstrap failed")).toBeInTheDocument();
    // The durable create still reset the draft to the pristine defaults.
    await waitFor(() => expect(name).toHaveValue(""));
    expect(screen.getByLabelText(/Location/)).toHaveValue("");
    expect(screen.getByLabelText(/Farm timezone/)).toHaveValue("Asia/Kolkata");
    const radios = screen.getByRole("radiogroup", { name: "Farm type" }).querySelectorAll("input");
    expect(radios[0].checked).toBe(true); // GOAT
    expect(radios[1].checked).toBe(false); // BUFFALO_DAIRY
  });
});

describe("FarmSelectPage — picker cards", () => {
  beforeEach(() => {
    pushMock.mockClear();
    navState.search = "";
  });

  it("labels each card with its species and timezone, defaulting the latter", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([GOAT_FARM, DAIRY_FARM])));
    await renderLoaded(2);

    const goat = cardOf("Test Goat Farm");
    expect(within(goat).getByText("Goat farm")).toBeInTheDocument();
    expect(within(goat).getByText("Asia/Kolkata")).toBeInTheDocument();
    expect(within(goat).getByText("current")).toBeInTheDocument();
    // Species icon: a goat herd shows the paw print, not the milk churn.
    expect(goat.querySelector("svg.lucide-paw-print")).not.toBeNull();
    expect(goat.querySelector("svg.lucide-milk")).toBeNull();
    // The type label and timezone are separated by a real " · ".
    const footer = goat.querySelector("p.text-xs") as HTMLElement;
    expect(footer.textContent).toBe("Goat farm · Asia/Kolkata");

    const dairy = cardOf("Navipet Dairy");
    expect(within(dairy).getByText("Buffalo dairy")).toBeInTheDocument();
    expect(within(dairy).getByText("Asia/Kolkata")).toBeInTheDocument();
    expect(within(dairy).queryByText("current")).not.toBeInTheDocument();
    // A buffalo dairy shows the milk icon.
    expect(dairy.querySelector("svg.lucide-milk")).not.toBeNull();
    expect(dairy.querySelector("svg.lucide-paw-print")).toBeNull();
    expect((dairy.querySelector("p.text-xs") as HTMLElement).textContent).toBe(
      "Buffalo dairy · Asia/Kolkata",
    );
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

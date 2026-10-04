import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  Sidebar,
  SidebarMenuButton,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";

const viewport = vi.hoisted(() => ({ isMobile: false }));

vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => viewport.isMobile }));

/** Surfaces the two independent open states the provider owns: the desktop
 * panel (`state`) and the mobile drawer (`openMobile`). */
function StateProbe() {
  const { state, openMobile } = useSidebar();
  return (
    <>
      <output aria-label="sidebar state">{state}</output>
      <output aria-label="drawer state">{openMobile ? "open" : "closed"}</output>
    </>
  );
}

function dispatchKeyDown(init: KeyboardEventInit) {
  const event = new KeyboardEvent("keydown", { cancelable: true, ...init });
  act(() => {
    window.dispatchEvent(event);
  });
  return event;
}

function sidebarState() {
  return screen.getByLabelText("sidebar state").textContent;
}

function drawerState() {
  return screen.getByLabelText("drawer state").textContent;
}

beforeEach(() => {
  viewport.isMobile = false;
});
describe("useSidebar", () => {
  it("names the missing provider instead of handing out a null context", () => {
    function Consumer() {
      useSidebar();
      return <p>never rendered</p>;
    }
    // React logs the render error itself; the assertion is on the throw.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => render(<Consumer />)).toThrow(
      "useSidebar must be used within a SidebarProvider.",
    );

    consoleError.mockRestore();
  });
});

describe("SidebarProvider", () => {
  it("starts expanded on desktop with the mobile drawer shut", () => {
    render(
      <SidebarProvider>
        <StateProbe />
      </SidebarProvider>,
    );

    expect(sidebarState()).toBe("expanded");
    expect(drawerState()).toBe("closed");
  });

  it("persists the desktop state in a week-long lax cookie", () => {
    const cookie = vi.spyOn(document, "cookie", "set");
    render(
      <SidebarProvider>
        <StateProbe />
        <SidebarTrigger />
      </SidebarProvider>,
    );

    act(() => {
      screen.getByRole("button", { name: "Toggle Sidebar" }).click();
    });

    // 60 * 60 * 24 * 7 — a week, so the choice survives a browser restart.
    expect(cookie).toHaveBeenCalledWith(
      "sidebar_state=false; path=/; max-age=604800; SameSite=Lax",
    );
    cookie.mockRestore();
  });

  it("toggles the drawer rather than the desktop panel on a small viewport", async () => {
    viewport.isMobile = true;
    const user = userEvent.setup();
    render(
      <SidebarProvider>
        <StateProbe />
        <SidebarTrigger />
      </SidebarProvider>,
    );
    const trigger = screen.getByRole("button", { name: "Toggle Sidebar" });

    await user.click(trigger);
    expect(drawerState()).toBe("open");
    expect(sidebarState()).toBe("expanded");

    await user.click(trigger);
    expect(drawerState()).toBe("closed");
  });

  it("leaves a drawer opened during the mounting commit open", () => {
    viewport.isMobile = true;
    function AutoOpenDrawer() {
      const { setOpenMobile } = useSidebar();
      useEffect(() => setOpenMobile(true), [setOpenMobile]);
      return null;
    }

    render(
      <SidebarProvider>
        <AutoOpenDrawer />
        <StateProbe />
      </SidebarProvider>,
    );

    // The viewport effect only closes the drawer when mobile is *left*.
    expect(drawerState()).toBe("open");
  });

  it("toggles from ctrl+b and swallows the browser default", () => {
    render(
      <SidebarProvider>
        <StateProbe />
      </SidebarProvider>,
    );

    const event = dispatchKeyDown({ key: "b", ctrlKey: true });

    expect(sidebarState()).toBe("collapsed");
    expect(event.defaultPrevented).toBe(true);
  });

  it("accepts the macOS meta+b spelling of the shortcut", () => {
    render(
      <SidebarProvider>
        <StateProbe />
      </SidebarProvider>,
    );

    const event = dispatchKeyDown({ key: "b", metaKey: true });

    expect(sidebarState()).toBe("collapsed");
    expect(event.defaultPrevented).toBe(true);
  });

  it("ignores an unmodified b and a modified key that is not b", () => {
    render(
      <SidebarProvider>
        <StateProbe />
      </SidebarProvider>,
    );

    // Typing "b" into a field must not fold the sidebar away.
    const typed = dispatchKeyDown({ key: "b" });
    expect(sidebarState()).toBe("expanded");
    expect(typed.defaultPrevented).toBe(false);

    const selectAll = dispatchKeyDown({ key: "a", ctrlKey: true });
    expect(sidebarState()).toBe("expanded");
    expect(selectAll.defaultPrevented).toBe(false);
  });

  it("stops listening for the shortcut once it unmounts", () => {
    const view = render(
      <SidebarProvider>
        <StateProbe />
      </SidebarProvider>,
    );
    view.unmount();

    const cookie = vi.spyOn(document, "cookie", "set");
    const event = dispatchKeyDown({ key: "b", ctrlKey: true });

    // A leaked listener would still run the toggle and rewrite the cookie.
    expect(cookie).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
    cookie.mockRestore();
  });
});

describe("Sidebar", () => {
  it("renders a plain always-visible panel when collapsible is none", () => {
    const { container } = render(
      <SidebarProvider>
        <Sidebar collapsible="none" className="custom-panel">
          <p>Panel body</p>
        </Sidebar>
      </SidebarProvider>,
    );

    const panel = container.querySelector('[data-slot="sidebar"]');
    expect(panel).toHaveClass("flex", "h-full", "flex-col", "custom-panel");
    expect(screen.getByText("Panel body")).toBeInTheDocument();
    // None of the collapsing chrome exists in this mode.
    expect(container.querySelector('[data-slot="sidebar-gap"]')).toBeNull();
    expect(container.querySelector('[data-slot="sidebar-container"]')).toBeNull();
    expect(panel).not.toHaveAttribute("data-state");
  });

  it("publishes the collapsed state and mode as data attributes on desktop", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <SidebarProvider>
        <Sidebar collapsible="icon">
          <p>Panel body</p>
        </Sidebar>
        <SidebarTrigger />
      </SidebarProvider>,
    );

    const root = container.querySelector('[data-slot="sidebar"]');
    expect(root).toHaveAttribute("data-state", "expanded");
    // Expanded: no collapse mode is advertised, so the icon-width rules stay off.
    expect(root).toHaveAttribute("data-collapsible", "");
    expect(root).toHaveAttribute("data-side", "left");
    expect(container.querySelector('[data-slot="sidebar-gap"]')).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Toggle Sidebar" }));

    expect(root).toHaveAttribute("data-state", "collapsed");
    expect(root).toHaveAttribute("data-collapsible", "icon");
  });

  it("renders the mobile sidebar as a labelled drawer dialog", async () => {
    viewport.isMobile = true;
    const user = userEvent.setup();
    const { container } = render(
      <SidebarProvider>
        <Sidebar>
          <p>Drawer body</p>
        </Sidebar>
        <SidebarTrigger />
      </SidebarProvider>,
    );

    expect(container.querySelector('[data-slot="sidebar-gap"]')).toBeNull();
    await user.click(screen.getByRole("button", { name: "Toggle Sidebar" }));

    const drawer = await screen.findByRole("dialog", { name: "Sidebar" });
    expect(drawer).toHaveAttribute("data-mobile", "true");
    expect(drawer).toHaveAttribute("data-slot", "sidebar");
    expect(screen.getByText("Displays the mobile sidebar.")).toBeInTheDocument();
    expect(screen.getByText("Drawer body")).toBeInTheDocument();
  });

  it("widens the collapsed rail for the floating and inset variants only", () => {
    const panel = (variant: "sidebar" | "floating" | "inset") => (
      <SidebarProvider>
        <Sidebar variant={variant} collapsible="icon">
          <p>Panel body</p>
        </Sidebar>
      </SidebarProvider>
    );
    const gap = () => document.querySelector('[data-slot="sidebar-gap"]');
    const container = () => document.querySelector('[data-slot="sidebar-container"]');

    const view = render(panel("sidebar"));
    // Flush against the viewport edge: icon width, no padding, a border instead.
    expect(gap()).toHaveClass("group-data-[collapsible=icon]:w-(--sidebar-width-icon)");
    expect(container()).toHaveClass("group-data-[side=left]:border-r");
    expect(container()).not.toHaveClass("p-2");

    view.rerender(panel("floating"));
    // Detached card: the gap has to account for the surrounding padding.
    expect(gap()).toHaveClass(
      "group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4)))]",
    );
    expect(container()).toHaveClass(
      "p-2",
      "group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4))+2px)]",
    );
    expect(container()).not.toHaveClass("group-data-[side=left]:border-r");

    view.rerender(panel("inset"));
    expect(gap()).toHaveClass(
      "group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4)))]",
    );
    expect(container()).toHaveClass("p-2");
    expect(container()).not.toHaveClass("group-data-[side=left]:border-r");
  });
});

describe("SidebarTrigger", () => {
  it("toggles the sidebar from the trigger with no onClick of its own", async () => {
    const user = userEvent.setup();
    render(
      <SidebarProvider>
        <StateProbe />
        <SidebarTrigger />
      </SidebarProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Toggle Sidebar" }));

    expect(sidebarState()).toBe("collapsed");
  });

  it("runs a caller's onClick as well as the toggle", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(
      <SidebarProvider>
        <StateProbe />
        <SidebarTrigger onClick={onClick} className="custom-trigger" />
      </SidebarProvider>,
    );

    const trigger = screen.getByRole("button", { name: "Toggle Sidebar" });
    expect(trigger).toHaveClass("custom-trigger");
    await user.click(trigger);

    expect(onClick).toHaveBeenCalledOnce();
    expect(onClick.mock.calls[0][0]).toHaveProperty("type", "click");
    expect(sidebarState()).toBe("collapsed");
  });
});


describe("SidebarMenuButton", () => {

  it("maps the variant and size props onto the button classes", () => {
    render(
      <SidebarProvider>
        <SidebarMenuButton>Default</SidebarMenuButton>
        <SidebarMenuButton variant="outline" size="lg">
          Outline
        </SidebarMenuButton>
      </SidebarProvider>,
    );

    const base = screen.getByRole("button", { name: "Default" });
    expect(base).toHaveClass("h-8", "text-sm");
    expect(base).toHaveAttribute("data-size", "default");

    const outline = screen.getByRole("button", { name: "Outline" });
    expect(outline).toHaveClass("bg-background", "h-12");
    expect(outline).toHaveAttribute("data-size", "lg");
  });

  it("shows a string tooltip only while the desktop sidebar is collapsed", async () => {
    const user = userEvent.setup();
    function Menu({ children }: { children: ReactNode }) {
      return <SidebarProvider defaultOpen={false}>{children}</SidebarProvider>;
    }
    render(
      <Menu>
        <SidebarMenuButton tooltip="Animals" render={<a href="#animals" />}>
          Animals
        </SidebarMenuButton>
      </Menu>,
    );

    // Focusing the collapsed icon must name it — that is the whole point of
    // the tooltip when only the icon is visible.
    await user.tab();
    const tip = await screen.findByText("Animals", {
      selector: '[data-slot="tooltip-content"]',
    });
    expect(tip).toBeVisible();
  });

  it("keeps the tooltip out of the way while the sidebar is expanded", async () => {
    const user = userEvent.setup();
    render(
      <SidebarProvider defaultOpen>
        <SidebarMenuButton tooltip="Animals">Animals</SidebarMenuButton>
      </SidebarProvider>,
    );

    await user.tab();
    const tip = await screen.findByText("Animals", {
      selector: '[data-slot="tooltip-content"]',
    });
    // The label is already on screen next to the icon.
    expect(tip).not.toBeVisible();
  });

  it("keeps the tooltip hidden on a touch viewport even when collapsed", async () => {
    viewport.isMobile = true;
    const user = userEvent.setup();
    render(
      <SidebarProvider defaultOpen={false}>
        <SidebarMenuButton tooltip="Animals">Animals</SidebarMenuButton>
      </SidebarProvider>,
    );

    await user.tab();
    const tip = await screen.findByText("Animals", {
      selector: '[data-slot="tooltip-content"]',
    });
    expect(tip).not.toBeVisible();
  });

  it("forwards a tooltip given as content props", async () => {
    const user = userEvent.setup();
    render(
      <SidebarProvider defaultOpen={false}>
        <SidebarMenuButton tooltip={{ children: "Tasks", className: "custom-tip" }}>
          Tasks
        </SidebarMenuButton>
      </SidebarProvider>,
    );

    await user.tab();
    const tip = await screen.findByText("Tasks", {
      selector: '[data-slot="tooltip-content"]',
    });
    expect(tip).toBeVisible();
    expect(tip).toHaveClass("custom-tip");
  });

  it("renders no tooltip machinery when no tooltip was asked for", async () => {
    const user = userEvent.setup();
    render(
      <SidebarProvider defaultOpen={false}>
        <SidebarMenuButton>Animals</SidebarMenuButton>
      </SidebarProvider>,
    );

    const button = screen.getByRole("button", { name: "Animals" });
    await user.hover(button);
    await user.tab();

    expect(document.querySelector('[data-slot="tooltip-content"]')).toBeNull();
    await waitFor(() =>
      expect(document.querySelector('[data-slot="tooltip-content"]')).toBeNull(),
    );
  });
});

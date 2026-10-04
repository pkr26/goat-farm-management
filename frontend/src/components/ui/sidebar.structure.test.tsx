import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Link from "next/link";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";

const viewport = vi.hoisted(() => ({ isMobile: false }));

vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => viewport.isMobile }));

beforeEach(() => {
  viewport.isMobile = false;
  // jsdom keeps cookies for the whole file; expire the one under test so each
  // case starts from "the sidebar has never been toggled".
  document.cookie = "sidebar_state=; path=/; max-age=0";
});

function StateProbe() {
  const { open, toggleSidebar } = useSidebar();
  return (
    <>
      <output aria-label="sidebar state">{open ? "expanded" : "collapsed"}</output>
      <button type="button" onClick={toggleSidebar}>
        Toggle
      </button>
    </>
  );
}

describe("SidebarProvider", () => {
  it("publishes the sidebar widths as custom properties on the wrapper", () => {
    const { container } = render(
      <SidebarProvider className="custom-wrapper">
        <span>content</span>
      </SidebarProvider>,
    );

    const wrapper = container.querySelector<HTMLElement>(
      '[data-slot="sidebar-wrapper"]',
    );
    expect(wrapper?.style.getPropertyValue("--sidebar-width")).toBe("16rem");
    expect(wrapper?.style.getPropertyValue("--sidebar-width-icon")).toBe("3rem");
    expect(wrapper).toHaveClass(
      "group/sidebar-wrapper",
      "flex",
      "min-h-svh",
      "w-full",
      "has-data-[variant=inset]:bg-sidebar",
      "custom-wrapper",
    );
  });

  it("persists every open/closed transition to the sidebar_state cookie", () => {
    render(
      <SidebarProvider defaultOpen>
        <StateProbe />
      </SidebarProvider>,
    );
    expect(document.cookie).not.toContain("sidebar_state");

    fireEvent.click(screen.getByRole("button", { name: "Toggle" }));
    expect(screen.getByLabelText("sidebar state")).toHaveTextContent("collapsed");
    expect(document.cookie).toContain("sidebar_state=false");

    fireEvent.click(screen.getByRole("button", { name: "Toggle" }));
    expect(screen.getByLabelText("sidebar state")).toHaveTextContent("expanded");
    expect(document.cookie).toContain("sidebar_state=true");
  });

  it("toggles on Meta/Ctrl+B and swallows the browser default for it", () => {
    render(
      <SidebarProvider defaultOpen>
        <StateProbe />
      </SidebarProvider>,
    );
    const state = screen.getByLabelText("sidebar state");

    // A bare "b" belongs to whatever has focus, so it must pass through.
    expect(fireEvent.keyDown(window, { key: "b" })).toBe(true);
    expect(state).toHaveTextContent("expanded");

    expect(fireEvent.keyDown(window, { key: "b", metaKey: true })).toBe(false);
    expect(state).toHaveTextContent("collapsed");

    expect(fireEvent.keyDown(window, { key: "b", ctrlKey: true })).toBe(false);
    expect(state).toHaveTextContent("expanded");

    // Another letter with the same modifier is not ours either.
    expect(fireEvent.keyDown(window, { key: "k", metaKey: true })).toBe(true);
    expect(state).toHaveTextContent("expanded");
  });

  it("detaches the shortcut listener once the provider unmounts", () => {
    const onOpenChange = vi.fn();
    const view = render(
      <SidebarProvider open onOpenChange={onOpenChange}>
        <span>content</span>
      </SidebarProvider>,
    );

    view.unmount();
    fireEvent.keyDown(window, { key: "b", metaKey: true });

    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("rebinds the shortcut to the change handler of the current render", () => {
    const firstHandler = vi.fn();
    const replacementHandler = vi.fn();
    const view = render(
      <SidebarProvider open={false} onOpenChange={firstHandler}>
        <span>content</span>
      </SidebarProvider>,
    );

    view.rerender(
      <SidebarProvider open={false} onOpenChange={replacementHandler}>
        <span>content</span>
      </SidebarProvider>,
    );
    fireEvent.keyDown(window, { key: "b", metaKey: true });

    expect(firstHandler).not.toHaveBeenCalled();
    expect(replacementHandler).toHaveBeenCalledOnce();
    expect(replacementHandler).toHaveBeenCalledWith(true);
  });

  it("names the provider in the error raised for an orphaned consumer", () => {
    function Orphan() {
      useSidebar();
      return null;
    }
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => render(<Orphan />)).toThrow(
      "useSidebar must be used within a SidebarProvider.",
    );

    consoleError.mockRestore();
  });

  it("opens the mobile drawer at the mobile width instead of the desktop shell", async () => {
    viewport.isMobile = true;
    render(
      <SidebarProvider>
        <SidebarTrigger />
        <Sidebar>drawer content</Sidebar>
      </SidebarProvider>,
    );
    expect(screen.queryByText("drawer content")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Toggle Sidebar" }));

    const drawer = await screen.findByRole("dialog", { name: "Sidebar" });
    expect(drawer.style.getPropertyValue("--sidebar-width")).toBe("18rem");
    expect(drawer).toHaveAttribute("data-mobile", "true");
    expect(drawer).toHaveAccessibleDescription("Displays the mobile sidebar.");
    expect(screen.getByText("drawer content")).toBeInTheDocument();
  });

  it("advertises the Meta/Ctrl+B shortcut on the trigger (aria-keyshortcuts)", () => {
    // The keydown listener lives on the provider; the trigger is where
    // assistive tech looks for the hint (2026-09-28 audit).
    render(
      <SidebarProvider>
        <SidebarTrigger />
        <Sidebar>panel</Sidebar>
      </SidebarProvider>,
    );

    expect(screen.getByRole("button", { name: "Toggle Sidebar" })).toHaveAttribute(
      "aria-keyshortcuts",
      "Meta+b Control+b",
    );
  });
});

describe("Sidebar", () => {
  it("reflects the open state and the default side/variant on the desktop shell", () => {
    const { container } = render(
      <SidebarProvider defaultOpen>
        <SidebarTrigger />
        <Sidebar>panel</Sidebar>
      </SidebarProvider>,
    );

    const shell = container.querySelector('[data-slot="sidebar"]');
    expect(shell).toHaveAttribute("data-state", "expanded");
    expect(shell).toHaveAttribute("data-side", "left");
    expect(shell).toHaveAttribute("data-variant", "sidebar");
    // Expanded means "no collapse mode in effect" — the attribute is present
    // but empty so `group-data-[collapsible=...]` never matches.
    expect(shell).toHaveAttribute("data-collapsible", "");

    fireEvent.click(screen.getByRole("button", { name: "Toggle Sidebar" }));

    expect(shell).toHaveAttribute("data-state", "collapsed");
    expect(shell).toHaveAttribute("data-collapsible", "offcanvas");
  });

  it("renders collapsible=none as a static panel with no collapse scaffolding", () => {
    const { container } = render(
      <SidebarProvider defaultOpen={false}>
        <Sidebar collapsible="none">static panel</Sidebar>
      </SidebarProvider>,
    );

    const panel = container.querySelector('[data-slot="sidebar"]');
    expect(panel).toHaveTextContent("static panel");
    expect(panel).toHaveClass(
      "flex",
      "h-full",
      "w-(--sidebar-width)",
      "flex-col",
      "bg-sidebar",
      "text-sidebar-foreground",
    );
    expect(panel).not.toHaveAttribute("data-state");
    expect(container.querySelector('[data-slot="sidebar-gap"]')).toBeNull();
    expect(container.querySelector('[data-slot="sidebar-container"]')).toBeNull();
  });

  it("sizes the gap and the fixed container for the plain sidebar variant", () => {
    const { container } = render(
      <SidebarProvider>
        <Sidebar className="custom-container">panel</Sidebar>
      </SidebarProvider>,
    );

    const gap = container.querySelector('[data-slot="sidebar-gap"]');
    expect(gap).toHaveClass(
      "relative",
      "w-(--sidebar-width)",
      "bg-transparent",
      "transition-[width]",
      "duration-200",
      "ease-linear",
    );
    expect(gap).toHaveClass("group-data-[collapsible=offcanvas]:w-0");
    expect(gap).toHaveClass("group-data-[side=right]:rotate-180");
    expect(gap).toHaveClass("group-data-[collapsible=icon]:w-(--sidebar-width-icon)");

    const fixed = container.querySelector('[data-slot="sidebar-container"]');
    expect(fixed).toHaveClass(
      "fixed",
      "inset-y-0",
      "z-10",
      "hidden",
      "h-svh",
      "w-(--sidebar-width)",
      "transition-[left,right,width]",
      "data-[side=left]:left-0",
      "data-[side=right]:right-0",
      "md:flex",
      "custom-container",
    );
    expect(fixed).toHaveClass(
      "group-data-[collapsible=icon]:w-(--sidebar-width-icon)",
      "group-data-[side=left]:border-r",
      "group-data-[side=right]:border-l",
    );
    expect(fixed).not.toHaveClass("p-2");
  });

  it("gives the floating variant its inset padding and wider icon rail", () => {
    const { container } = render(
      <SidebarProvider>
        <Sidebar variant="floating" side="right">
          panel
        </Sidebar>
      </SidebarProvider>,
    );

    expect(container.querySelector('[data-slot="sidebar"]')).toHaveAttribute(
      "data-variant",
      "floating",
    );
    const gap = container.querySelector('[data-slot="sidebar-gap"]');
    expect(gap).toHaveClass(
      "group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4)))]",
    );
    expect(gap).not.toHaveClass("group-data-[collapsible=icon]:w-(--sidebar-width-icon)");

    const fixed = container.querySelector('[data-slot="sidebar-container"]');
    expect(fixed).toHaveAttribute("data-side", "right");
    expect(fixed).toHaveClass(
      "p-2",
      "group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4))+2px)]",
    );
    expect(fixed).not.toHaveClass(
      "group-data-[side=left]:border-r",
      "group-data-[side=right]:border-l",
    );
  });

  it("gives the inset variant the same geometry as the floating variant", () => {
    const { container } = render(
      <SidebarProvider>
        <Sidebar variant="inset">panel</Sidebar>
      </SidebarProvider>,
    );

    expect(container.querySelector('[data-slot="sidebar"]')).toHaveAttribute(
      "data-variant",
      "inset",
    );
    expect(container.querySelector('[data-slot="sidebar-gap"]')).toHaveClass(
      "group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4)))]",
    );
    const fixed = container.querySelector('[data-slot="sidebar-container"]');
    expect(fixed).toHaveClass(
      "p-2",
      "group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4))+2px)]",
    );
    expect(fixed).not.toHaveClass("group-data-[side=left]:border-r");
  });
});
describe("sidebar layout primitives", () => {
  it("renders the inset as the page main region", () => {
    render(<SidebarInset className="custom-inset">page body</SidebarInset>);

    const main = screen.getByRole("main");
    expect(main).toHaveAttribute("data-slot", "sidebar-inset");
    expect(main).toHaveClass(
      "relative",
      "flex",
      "min-w-0",
      "w-full",
      "flex-1",
      "flex-col",
      "bg-background",
      "md:peer-data-[variant=inset]:m-2",
      "md:peer-data-[variant=inset]:ml-0",
      "md:peer-data-[variant=inset]:rounded-xl",
      "custom-inset",
    );
  });

  it("stacks the header and the footer with the same padded column", () => {
    const { container } = render(
      <>
        <SidebarHeader className="custom-header">header</SidebarHeader>
        <SidebarFooter className="custom-footer">footer</SidebarFooter>
      </>,
    );

    const header = container.querySelector('[data-slot="sidebar-header"]');
    expect(header).toHaveAttribute("data-sidebar", "header");
    expect(header).toHaveClass("flex", "flex-col", "gap-2", "p-2", "custom-header");

    const footer = container.querySelector('[data-slot="sidebar-footer"]');
    expect(footer).toHaveAttribute("data-sidebar", "footer");
    expect(footer).toHaveClass("flex", "flex-col", "gap-2", "p-2", "custom-footer");
  });

  it("makes the content region a scrollable landmark", () => {
    render(<SidebarContent aria-label="Farm sections">links</SidebarContent>);

    const nav = screen.getByRole("navigation", { name: "Farm sections" });
    expect(nav).toHaveAttribute("data-slot", "sidebar-content");
    expect(nav).toHaveAttribute("data-sidebar", "content");
    expect(nav).toHaveClass(
      "no-scrollbar",
      "flex",
      "min-h-0",
      "flex-1",
      "flex-col",
      "gap-0",
      "overflow-auto",
      "group-data-[collapsible=icon]:overflow-hidden",
    );
  });

  it("renders groups, group content, menus and menu items as the nav skeleton", () => {
    const { container } = render(
      <SidebarGroup className="custom-group">
        <SidebarGroupContent className="custom-group-content">
          <SidebarMenu className="custom-menu">
            <SidebarMenuItem className="custom-menu-item">entry</SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>,
    );

    expect(container.querySelector('[data-slot="sidebar-group"]')).toHaveClass(
      "relative",
      "flex",
      "w-full",
      "min-w-0",
      "flex-col",
      "p-2",
      "custom-group",
    );
    expect(container.querySelector('[data-slot="sidebar-group-content"]')).toHaveClass(
      "w-full",
      "text-sm",
      "custom-group-content",
    );

    const menu = screen.getByRole("list");
    expect(menu).toHaveAttribute("data-slot", "sidebar-menu");
    expect(menu).toHaveClass("flex", "w-full", "min-w-0", "flex-col", "gap-0", "custom-menu");

    const item = screen.getByRole("listitem");
    expect(item).toHaveAttribute("data-slot", "sidebar-menu-item");
    expect(item).toHaveClass("group/menu-item", "relative", "custom-menu-item");
  });
});

describe("SidebarGroupLabel", () => {
  it("renders a labelled div that collapses away in icon mode", () => {
    render(<SidebarGroupLabel className="custom-label">Livestock</SidebarGroupLabel>);

    const label = screen.getByText("Livestock");
    expect(label.tagName).toBe("DIV");
    expect(label).toHaveAttribute("data-slot", "sidebar-group-label");
    expect(label).toHaveAttribute("data-sidebar", "group-label");
    expect(label).toHaveClass(
      "flex",
      "h-8",
      "shrink-0",
      "items-center",
      "rounded-md",
      "px-2",
      "text-xs",
      "font-medium",
      "text-sidebar-foreground/70",
      "ring-sidebar-ring",
      "outline-hidden",
      "transition-[margin,opacity]",
      "duration-200",
      "ease-linear",
      "group-data-[collapsible=icon]:-mt-8",
      "group-data-[collapsible=icon]:opacity-0",
      "focus-visible:ring-2",
      "custom-label",
    );
  });

  it("keeps its slot wiring when rendered through a caller element", () => {
    render(<SidebarGroupLabel render={<h2 />}>Herd</SidebarGroupLabel>);

    const heading = screen.getByRole("heading", { name: "Herd", level: 2 });
    expect(heading).toHaveAttribute("data-slot", "sidebar-group-label");
    expect(heading).toHaveAttribute("data-sidebar", "group-label");
    expect(heading).toHaveClass("flex", "h-8", "items-center");
  });
});


describe("SidebarMenuButton", () => {
  it("renders the default menu button contract", () => {
    render(
      <SidebarProvider>
        <SidebarMenuButton>Animals</SidebarMenuButton>
      </SidebarProvider>,
    );

    const button = screen.getByRole("button", { name: "Animals" });
    expect(button).toHaveAttribute("data-slot", "sidebar-menu-button");
    expect(button).toHaveAttribute("data-sidebar", "menu-button");
    expect(button).toHaveAttribute("data-size", "default");
    expect(button).not.toHaveAttribute("data-active");
    expect(button).toHaveClass(
      "peer/menu-button",
      "group/menu-button",
      "flex",
      "w-full",
      "items-center",
      "gap-2",
      "overflow-hidden",
      "rounded-md",
      "p-2",
      "text-left",
      "ring-sidebar-ring",
      "outline-hidden",
      "transition-[width,height,padding]",
      "group-data-[collapsible=icon]:size-8!",
      "group-data-[collapsible=icon]:p-2!",
      "aria-disabled:pointer-events-none",
      "data-active:bg-sidebar-accent",
      "[&>span:last-child]:truncate",
    );
    // The default size variant, not the base string, supplies the height.
    expect(button).toHaveClass("h-8", "text-sm");
  });

  it("maps the size and variant props onto the menu-button variants", () => {
    render(
      <SidebarProvider>
        <SidebarMenuButton size="sm">Small</SidebarMenuButton>
        <SidebarMenuButton size="lg" isActive>
          Large
        </SidebarMenuButton>
        <SidebarMenuButton variant="outline">Outline</SidebarMenuButton>
      </SidebarProvider>,
    );

    const small = screen.getByRole("button", { name: "Small" });
    expect(small).toHaveAttribute("data-size", "sm");
    expect(small).toHaveClass("h-7", "text-xs");
    expect(small).not.toHaveClass("h-8");

    const large = screen.getByRole("button", { name: "Large" });
    expect(large).toHaveAttribute("data-size", "lg");
    expect(large).toHaveAttribute("data-active", "");
    expect(large).toHaveClass("h-12", "text-sm", "group-data-[collapsible=icon]:p-0!");
    expect(large).not.toHaveClass("h-8");

    const outline = screen.getByRole("button", { name: "Outline" });
    expect(outline).toHaveClass(
      "bg-background",
      "shadow-[0_0_0_1px_var(--sidebar-border)]",
      "hover:shadow-[0_0_0_1px_var(--sidebar-accent)]",
    );
    expect(small).not.toHaveClass("bg-background");
  });

  it("renders through a caller element while keeping the menu-button styling", () => {
    render(
      <SidebarProvider>
        <SidebarMenuButton render={<Link href="/animals/1" />} className="custom-menu-button">
          Go to animals
        </SidebarMenuButton>
      </SidebarProvider>,
    );

    const link = screen.getByRole("link", { name: "Go to animals" });
    expect(link).toHaveAttribute("href", "/animals/1");
    expect(link).toHaveAttribute("data-sidebar", "menu-button");
    expect(link).toHaveClass("custom-menu-button", "peer/menu-button", "h-8");
  });

  it("shows a string tooltip only while the desktop sidebar is collapsed", async () => {
    const user = userEvent.setup();
    render(
      <SidebarProvider defaultOpen={false}>
        <SidebarMenuButton tooltip="Herd overview">Animals</SidebarMenuButton>
      </SidebarProvider>,
    );

    await user.tab();

    const tip = await screen.findByText("Herd overview");
    expect(tip).toHaveAttribute("data-slot", "tooltip-content");
    expect(tip).toBeVisible();
  });

  it("keeps the tooltip hidden while the sidebar is expanded", async () => {
    const user = userEvent.setup();
    render(
      <SidebarProvider defaultOpen>
        <SidebarMenuButton tooltip="Herd overview">Animals</SidebarMenuButton>
      </SidebarProvider>,
    );

    await user.tab();

    await waitFor(() => expect(screen.getByText("Herd overview")).not.toBeVisible());
  });

  it("lets a tooltip config object override the placement defaults", async () => {
    const user = userEvent.setup();
    render(
      <SidebarProvider defaultOpen={false}>
        <SidebarMenuButton tooltip={{ children: "Feeding plan", side: "left" }}>
          Feeding
        </SidebarMenuButton>
      </SidebarProvider>,
    );

    await user.tab();

    const tip = await screen.findByText("Feeding plan");
    expect(tip).toHaveAttribute("data-side", "left");
    expect(tip).toBeVisible();
  });
});

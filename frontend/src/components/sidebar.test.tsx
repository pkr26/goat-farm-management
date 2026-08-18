import { renderToString } from "react-dom/server";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  SidebarMenuSkeleton,
  SidebarProvider,
  useSidebar,
} from "@/components/ui/sidebar";

const viewport = vi.hoisted(() => ({ isMobile: false }));

vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => viewport.isMobile }));

describe("SidebarMenuSkeleton", () => {
  it("renders deterministic markup for server rendering and hydration", () => {
    const random = vi.spyOn(Math, "random");
    random.mockReturnValue(0);
    const serverMarkup = renderToString(<SidebarMenuSkeleton showIcon />);

    random.mockReturnValue(0.999);
    const hydrationMarkup = renderToString(<SidebarMenuSkeleton showIcon />);

    expect(hydrationMarkup).toBe(serverMarkup);
    expect(serverMarkup).toContain("max-w-[70%]");
    expect(random).not.toHaveBeenCalled();
    random.mockRestore();
  });

  it("renders the icon branch only when requested and preserves caller classes", () => {
    const { container, rerender } = render(
      <SidebarMenuSkeleton className="custom-skeleton" />,
    );
    const wrapper = container.querySelector('[data-sidebar="menu-skeleton"]');
    expect(wrapper).toHaveClass(
      "custom-skeleton",
      "flex",
      "h-8",
      "items-center",
      "gap-2",
      "rounded-md",
      "px-2",
    );
    expect(container.querySelector('[data-sidebar="menu-skeleton-icon"]')).toBeNull();
    expect(container.querySelector('[data-sidebar="menu-skeleton-text"]')).toHaveClass(
      "max-w-[70%]",
    );

    rerender(<SidebarMenuSkeleton showIcon />);
    expect(container.querySelector('[data-sidebar="menu-skeleton-icon"]')).toHaveClass(
      "size-4",
      "rounded-md",
    );
  });
});

describe("SidebarProvider", () => {
  beforeEach(() => {
    viewport.isMobile = false;
  });

  it("composes multiple toggles delivered before the next render", () => {
    function Probe() {
      const { open, toggleSidebar } = useSidebar();
      return (
        <>
          <output aria-label="sidebar state">{open ? "expanded" : "collapsed"}</output>
          <button
            type="button"
            onClick={() => {
              toggleSidebar();
              toggleSidebar();
            }}
          >
            Toggle twice
          </button>
        </>
      );
    }

    render(
      <SidebarProvider defaultOpen>
        <Probe />
      </SidebarProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Toggle twice" }));

    expect(screen.getByLabelText("sidebar state")).toHaveTextContent("expanded");
  });

  it("does not reopen a stale mobile drawer after a desktop round trip", async () => {
    function Probe() {
      const { openMobile, setOpenMobile } = useSidebar();
      return (
        <>
          <output aria-label="mobile drawer state">{openMobile ? "open" : "closed"}</output>
          <button type="button" onClick={() => setOpenMobile(true)}>
            Open mobile drawer
          </button>
        </>
      );
    }

    viewport.isMobile = true;
    const view = render(
      <SidebarProvider>
        <Probe />
      </SidebarProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open mobile drawer" }));
    expect(screen.getByLabelText("mobile drawer state")).toHaveTextContent("open");

    viewport.isMobile = false;
    view.rerender(
      <SidebarProvider>
        <Probe />
      </SidebarProvider>,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("mobile drawer state")).toHaveTextContent("closed"),
    );

    viewport.isMobile = true;
    view.rerender(
      <SidebarProvider>
        <Probe />
      </SidebarProvider>,
    );
    expect(screen.getByLabelText("mobile drawer state")).toHaveTextContent("closed");
  });
});

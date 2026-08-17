import { renderToString } from "react-dom/server";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SidebarMenuSkeleton } from "@/components/ui/sidebar";

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

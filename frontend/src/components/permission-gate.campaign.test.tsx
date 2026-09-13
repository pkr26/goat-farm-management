/**
 * PermissionGate — fresh-domain mutation campaign kills (2026-09): the
 * either-part no-access override contract, its default title, and the
 * loading skeleton's stat count.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PermissionsState } from "@/lib/use-permissions";

import { PermissionGate } from "./permission-gate";

const deniedPerms = (loading: boolean): PermissionsState => ({
  loading,
  isError: false,
  error: null,
  refetch: vi.fn(),
  isOwner: false,
  can: () => false,
});

describe("PermissionGate — campaign kills", () => {
  it("uses the richer no-access treatment when only a title override is given", () => {
    render(
      <PermissionGate
        perms={deniedPerms(false)}
        perm="animals.view"
        label="Animals"
        description="Herd register"
        noAccessTitle="Ask your farm owner"
      >
        <div>content</div>
      </PermissionGate>,
    );

    // The dashed EmptyState card carries the override; the plain paragraph
    // treatment must not win when either override part is present.
    expect(screen.getByText("Ask your farm owner")).toBeInTheDocument();
    expect(screen.getByText("Ask your farm owner").closest("div")).not.toHaveClass(
      "text-muted-foreground",
    );
  });

  it("falls back to the default title when only a description override is given", () => {
    render(
      <PermissionGate
        perms={deniedPerms(false)}
        perm="animals.view"
        label="Animals"
        description="Herd register"
        noAccessDescription="Your role cannot view the herd register."
      >
        <div>content</div>
      </PermissionGate>,
    );

    expect(
      screen.getByText("You don't have access to this page."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Your role cannot view the herd register."),
    ).toBeInTheDocument();
  });

  it("renders no stat placeholders when the page declared none", () => {
    const { container } = render(
      <PermissionGate
        perms={deniedPerms(true)}
        perm="animals.view"
        label="Animals"
        description="Herd register"
      >
        <div>content</div>
      </PermissionGate>,
    );

    // The size-10 icon block only exists inside a stat placeholder.
    expect(container.querySelector(".size-10")).toBeNull();
    // Sanity: the declared-stat spelling does render exactly one.
    const { container: withStats } = render(
      <PermissionGate
        perms={deniedPerms(true)}
        perm="animals.view"
        label="Animals"
        description="Herd register"
        stats={1}
      >
        <div>content</div>
      </PermissionGate>,
    );
    expect(withStats.querySelectorAll(".size-10")).toHaveLength(1);
  });
});

import { render, screen } from "@testing-library/react";
import { Activity, PawPrint, Plus } from "lucide-react";
import { describe, expect, it } from "vitest";

import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { Logo } from "@/components/logo";
import { PageHeader } from "@/components/page-header";
import { StatCard } from "@/components/stat-card";
import { StatusBadge } from "@/components/status-badge";

describe("shared display components", () => {
  it("renders PageHeader content, actions, and valid numeric-zero nodes", () => {
    render(
      <PageHeader
        title="Animals"
        description={0}
        actions={<button type="button">Add animal</button>}
        className="custom-header"
      />,
    );

    const heading = screen.getByRole("heading", { level: 1, name: "Animals" });
    expect(heading.parentElement?.parentElement).toHaveClass("custom-header");
    expect(screen.getByText("0")).toHaveClass("text-muted-foreground");
    expect(screen.getByRole("button", { name: "Add animal" })).toBeInTheDocument();
  });

  it("omits optional PageHeader wrappers when their content is absent", () => {
    const { container } = render(<PageHeader title="Animals" />);

    expect(container.querySelectorAll("p")).toHaveLength(0);
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("renders DataTableCard's header branches and always renders its body", () => {
    const { container, rerender } = render(
      <DataTableCard title={0} description="Latest rows" actions={<button>Export</button>}>
        <p>Row data</p>
      </DataTableCard>,
    );

    expect(screen.getByText("0")).toHaveAttribute("data-slot", "card-title");
    expect(screen.getByText("Latest rows")).toHaveAttribute("data-slot", "card-description");
    expect(screen.getByRole("button", { name: "Export" }).parentElement).toHaveAttribute(
      "data-slot",
      "card-action",
    );
    expect(screen.getByText("Row data")).toBeInTheDocument();

    rerender(<DataTableCard>Only body</DataTableCard>);
    expect(container.querySelector('[data-slot="card-header"]')).toBeNull();
    expect(screen.getByText("Only body")).toHaveAttribute("data-slot", "card-content");
  });

  it("renders EmptyState's optional branches including numeric zero", () => {
    const { container, rerender } = render(
      <EmptyState icon={PawPrint} title="No goats" description={0} className="custom-empty">
        <button type="button">Add one</button>
      </EmptyState>,
    );

    expect(screen.getByRole("heading", { level: 3, name: "No goats" })).toBeInTheDocument();
    expect(screen.getByText("0")).toHaveClass("text-muted-foreground");
    expect(screen.getByRole("button", { name: "Add one" })).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("custom-empty");

    rerender(<EmptyState icon={Plus} title="Nothing here" />);
    expect(container.querySelectorAll("p")).toHaveLength(0);
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("renders StatCard trend direction, tone, hints, and tint", () => {
    const { rerender } = render(
      <StatCard
        label="Active goats"
        value={12}
        icon={Activity}
        tint="emerald"
        trend={{ value: "+2", direction: "up", tone: "positive" }}
        hint="since last month"
      />,
    );

    expect(screen.getByText("12")).toBeInTheDocument();
    const trend = screen.getByText("+2").closest("span");
    expect(trend).toHaveClass("text-emerald-600");
    expect(trend?.querySelector(".lucide-trending-up")).toBeInTheDocument();
    expect(screen.getByText("since last month")).toBeInTheDocument();
    expect(screen.getByText("Active goats").parentElement?.previousElementSibling).toHaveClass(
      "bg-emerald-100",
    );

    rerender(
      <StatCard
        label="Losses"
        value={1}
        icon={Activity}
        trend={{ value: "-1", direction: "down", tone: "negative" }}
        hint={0}
      />,
    );
    const negative = screen.getByText("-1").closest("span");
    expect(negative).toHaveClass("text-red-600");
    expect(negative?.querySelector(".lucide-trending-down")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();

    rerender(<StatCard label="Plain hint" value={3} icon={Activity} hint={0} />);
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(document.querySelector(".lucide-trending-up")).not.toBeInTheDocument();
    expect(document.querySelector(".lucide-trending-down")).not.toBeInTheDocument();

    rerender(
      <StatCard
        label="Neutral trend"
        value={4}
        icon={Activity}
        trend={{ value: "steady", direction: "up" }}
      />,
    );
    expect(screen.getByText("steady").closest("span")).toHaveClass("text-muted-foreground");

    rerender(<StatCard label="No context" value={5} icon={Activity} />);
    expect(screen.getByText("No context").parentElement).not.toHaveTextContent("steady");
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("normalizes known status spellings and humanizes unknown statuses", () => {
    const { rerender } = render(<StatusBadge status=" awaiting-verification " />);
    let badge = screen.getByText("Awaiting Verification");
    expect(badge).toHaveClass("bg-amber-100");
    expect(badge).toHaveAttribute("data-variant", "outline");

    rerender(<StatusBadge status="custom_state" className="custom-badge" />);
    badge = screen.getByText("Custom State");
    expect(badge).toHaveAttribute("data-variant", "secondary");
    expect(badge).toHaveClass("custom-badge");

    rerender(<StatusBadge status="ACTIVE">Ready now</StatusBadge>);
    expect(screen.getByText("Ready now")).toHaveClass("bg-emerald-100");
  });

  it("renders the logo mark with an optional wordmark", () => {
    const { container, rerender } = render(<Logo className="custom-logo" />);
    expect(screen.getByText("GoatFarm")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("custom-logo");
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");

    rerender(<Logo withWordmark={false} />);
    expect(screen.queryByText("GoatFarm")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeInTheDocument();
  });
});

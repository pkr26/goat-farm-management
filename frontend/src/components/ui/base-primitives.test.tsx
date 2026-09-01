/**
 * The primitive layer the whole app is assembled from: the Button/Badge
 * variant maps, the Card and Dialog slot structure, the small form and
 * feedback primitives, and the Providers tree that hands every page its query
 * client, theme, auth session and toaster.
 *
 * None of these carry domain logic, so what is worth pinning is the contract
 * the pages and the stylesheet actually consume: which variant/size prop
 * yields which visual class, which `data-slot` each part carries (the CSS
 * `has-data-[slot=…]`/`in-data-[slot=…]` rules key off them), the ARIA and
 * keyboard wiring, the `render` composition point, and the defaults that
 * apply when a caller passes nothing — `buttonVariants()` called bare, a Card
 * with no size, a dialog footer with no close button.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useQueryClient, type DefaultOptions } from "@tanstack/react-query";
import Link from "next/link";
import { toast } from "sonner";
import type { ReactNode } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { Providers } from "@/components/providers";
import { Badge, badgeVariants } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/sonner";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth-context";
import { TEST_USER } from "@/test/msw-server";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom has no matchMedia; next-themes' and sonner's system-theme detection
  // both need it, and it must report light so `data-sonner-theme` is stable.
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
  }));
});

/** cva hands back one space-separated string; comparing it as a set keeps the
 *  assertions readable as "the map contains these classes" rather than
 *  freezing the whole utility soup in a literal. */
function classesOf(value: string): string[] {
  return value.split(/\s+/).filter(Boolean);
}

function slot(name: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`[data-slot="${name}"]`);
  if (!element) {
    throw new Error(`No element with data-slot="${name}" is rendered`);
  }
  return element;
}

type ButtonVariant =
  | "default"
  | "outline"
  | "secondary"
  | "ghost"
  | "destructive"
  | "link";
type ButtonSize =
  | "default"
  | "xs"
  | "sm"
  | "lg"
  | "icon"
  | "icon-xs"
  | "icon-sm"
  | "icon-lg";

/** One representative class per variant — enough to prove the branch of the
 *  map was taken, without asserting Tailwind strings that carry no contract. */
const BUTTON_VARIANT_CLASSES: [ButtonVariant, string[]][] = [
  ["default", ["bg-primary", "text-primary-foreground", "hover:bg-primary/80"]],
  ["outline", ["border-border", "bg-background", "dark:bg-input/30"]],
  ["secondary", ["bg-secondary", "text-secondary-foreground"]],
  ["ghost", ["hover:bg-muted", "dark:hover:bg-muted/50"]],
  ["destructive", ["bg-destructive/10", "text-destructive"]],
  ["link", ["text-primary", "underline-offset-4", "hover:underline"]],
];

const BUTTON_SIZE_CLASSES: [ButtonSize, string[]][] = [
  ["default", ["h-9", "px-3.5"]],
  ["xs", ["h-6", "px-2", "text-xs"]],
  ["sm", ["h-8", "text-[0.8rem]"]],
  ["lg", ["h-10", "px-5"]],
  ["icon", ["size-9"]],
  ["icon-xs", ["size-6", "rounded-[min(var(--radius-md),10px)]"]],
  ["icon-sm", ["size-8", "rounded-[min(var(--radius-md),12px)]"]],
  ["icon-lg", ["size-10"]],
];

describe("buttonVariants", () => {
  it("falls back to the default variant and size when called bare", () => {
    // Pages hand this straight to <Link className={buttonVariants()}>, so the
    // no-argument call has to produce a complete, styled button.
    const classes = classesOf(buttonVariants());

    expect(classes).toEqual(
      expect.arrayContaining([
        "inline-flex",
        "items-center",
        "justify-center",
        "rounded-lg",
        "text-sm",
        "font-medium",
        "disabled:opacity-50",
        "bg-primary",
        "text-primary-foreground",
        "h-9",
      ]),
    );
  });

  it("keeps the default size when only a variant is named", () => {
    // not-found.tsx / no-access ask for `{ variant: "outline" }` alone.
    const classes = classesOf(buttonVariants({ variant: "outline" }));

    expect(classes).toEqual(expect.arrayContaining(["border-border", "h-9"]));
    expect(classes).not.toContain("bg-primary");
  });

  it("keeps the default variant when only a size is named", () => {
    // tasks/page.tsx asks for `{ size: "sm" }` alone.
    const classes = classesOf(buttonVariants({ size: "sm" }));

    expect(classes).toEqual(expect.arrayContaining(["bg-primary", "h-8"]));
    expect(classes).not.toContain("h-9");
  });
});

describe("Button", () => {
  it.each(BUTTON_VARIANT_CLASSES)(
    "paints the %s variant",
    (variant, expected) => {
      render(<Button variant={variant}>Save</Button>);

      const button = screen.getByRole("button", { name: "Save" });
      expect(button).toHaveAttribute("data-slot", "button");
      expect(button).toHaveClass(...expected);
    },
  );

  it.each(BUTTON_SIZE_CLASSES)("sizes the %s button", (size, expected) => {
    render(<Button size={size}>Save</Button>);

    expect(screen.getByRole("button", { name: "Save" })).toHaveClass(
      ...expected,
    );
  });

  it("renders the default variant and size with no props at all", () => {
    render(<Button>Save</Button>);

    expect(screen.getByRole("button", { name: "Save" })).toHaveClass(
      "bg-primary",
      "text-primary-foreground",
      "h-9",
    );
  });

  it("lets a caller's own utility beat the variant it collides with", () => {
    render(
      <Button className="h-12 bg-red-500">Save</Button>,
    );

    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toHaveClass("h-12", "bg-red-500");
    // tailwind-merge drops the losing side, otherwise the override would be a
    // coin toss on stylesheet order.
    expect(button).not.toHaveClass("h-9");
    expect(button).not.toHaveClass("bg-primary");
  });

  it("fires onClick when pressed and stays silent while disabled", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { rerender } = render(<Button onClick={onClick}>Save</Button>);

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onClick).toHaveBeenCalledTimes(1);

    rerender(
      <Button disabled onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toBeDisabled();

    await user.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("keeps the button styling when rendered as another element", () => {
    // Dialog/Sheet close buttons and the sidebar compose through `render`.
    render(
      <Button render={<Link href="/animals" />} variant="outline" size="sm">
        Animals
      </Button>,
    );

    const link = screen.getByRole("link", { name: "Animals" });
    expect(link).toHaveAttribute("data-slot", "button");
    expect(link).toHaveClass("border-border", "h-8");
  });
});

describe("Badge", () => {
  it.each([
    ["default", ["bg-primary", "text-primary-foreground"]],
    ["secondary", ["bg-secondary", "text-secondary-foreground"]],
    ["destructive", ["bg-destructive/10", "text-destructive"]],
    ["outline", ["border-border", "text-foreground"]],
    ["ghost", ["hover:bg-muted", "hover:text-muted-foreground"]],
    ["link", ["text-primary", "underline-offset-4", "hover:underline"]],
  ] as [
    "default" | "secondary" | "destructive" | "outline" | "ghost" | "link",
    string[],
  ][])("paints the %s variant and publishes it as state", (variant, expected) => {
    render(<Badge variant={variant}>12 head</Badge>);

    const badge = screen.getByText("12 head");
    expect(badge).toHaveAttribute("data-slot", "badge");
    expect(badge).toHaveAttribute("data-variant", variant);
    expect(badge).toHaveClass(...expected);
  });

  it("defaults to the default variant, in both the class map and the state", () => {
    render(<Badge>12 head</Badge>);

    const badge = screen.getByText("12 head");
    expect(badge.tagName).toBe("SPAN");
    expect(badge).toHaveAttribute("data-variant", "default");
    expect(badge).toHaveClass(
      "inline-flex",
      "rounded-4xl",
      "text-xs",
      "whitespace-nowrap",
      "bg-primary",
    );
  });

  it("falls back to the default variant when badgeVariants is called bare", () => {
    expect(classesOf(badgeVariants())).toEqual(
      expect.arrayContaining(["inline-flex", "rounded-4xl", "bg-primary"]),
    );
  });

  it("merges a caller's className and composes into another element", () => {
    render(
      <Badge variant="outline" className="ml-2" render={<Link href="/tasks" />}>
        3 open
      </Badge>,
    );

    const badge = screen.getByRole("link", { name: "3 open" });
    expect(badge).toHaveClass("inline-flex", "border-border", "ml-2");
  });
});

describe("Card", () => {
  it("gives every part its own slot and the default spacing size", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Herd summary</CardTitle>
          <CardDescription>Live counts for this farm.</CardDescription>
          <CardAction>
            <Button size="sm">Refresh</Button>
          </CardAction>
        </CardHeader>
        <CardContent>42 head</CardContent>
        <CardFooter>Updated a minute ago</CardFooter>
      </Card>,
    );

    const card = slot("card");
    expect(card).toHaveAttribute("data-size", "default");
    expect(card).toHaveClass(
      "group/card",
      "flex",
      "flex-col",
      "rounded-xl",
      "bg-card",
      // The footer's flush bottom edge is driven off the footer's own slot.
      "has-data-[slot=card-footer]:pb-0",
    );

    expect(slot("card-header")).toHaveClass(
      "grid",
      "px-(--card-spacing)",
      "has-data-[slot=card-action]:grid-cols-[1fr_auto]",
    );
    expect(slot("card-title")).toHaveClass("font-heading", "font-medium");
    expect(slot("card-description")).toHaveClass(
      "text-sm",
      "text-muted-foreground",
    );
    expect(slot("card-action")).toHaveClass("col-start-2", "justify-self-end");
    expect(slot("card-content")).toHaveClass("px-(--card-spacing)");

    const footer = slot("card-footer");
    expect(footer).toHaveTextContent("Updated a minute ago");
    expect(footer).toHaveClass("flex", "items-center", "border-t", "bg-muted/50");
  });

  it("switches to the compact spacing scale at size sm", () => {
    render(<Card size="sm">compact</Card>);

    // `data-[size=sm]:[--card-spacing:…]` is the only thing that shrinks the
    // card, so the attribute is the whole contract.
    expect(slot("card")).toHaveAttribute("data-size", "sm");
  });
});

describe("Dialog", () => {
  async function openDialog(content: ReactNode) {
    const user = userEvent.setup();
    render(
      <Dialog>
        <DialogTrigger>Move animal</DialogTrigger>
        {content}
      </Dialog>,
    );

    await user.click(screen.getByRole("button", { name: "Move animal" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("opens from the trigger and is named by its title and description", async () => {
    const { dialog } = await openDialog(
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Move animal</DialogTitle>
          <DialogDescription>Pick a destination bucket.</DialogDescription>
        </DialogHeader>
      </DialogContent>,
    );

    expect(dialog).toHaveAttribute("data-slot", "dialog-content");
    expect(dialog).toHaveAccessibleName("Move animal");
    expect(dialog).toHaveAccessibleDescription("Pick a destination bucket.");
    expect(dialog).toHaveClass(
      "fixed",
      "z-50",
      "rounded-xl",
      "bg-popover",
      "overflow-y-auto",
    );

    expect(slot("dialog-header")).toHaveClass("flex", "flex-col", "gap-2");
    expect(slot("dialog-title")).toHaveClass("font-heading", "font-medium");
    expect(slot("dialog-description")).toHaveClass(
      "text-sm",
      "text-muted-foreground",
    );
  });

  it("lays a dimming backdrop over the page while open", async () => {
    await openDialog(
      <DialogContent>
        <DialogTitle>Move animal</DialogTitle>
      </DialogContent>,
    );

    expect(slot("dialog-overlay")).toHaveClass(
      "fixed",
      "inset-0",
      "z-50",
      "bg-black/10",
    );
  });

  it("closes from the corner close button, and omits it on request", async () => {
    const { user } = await openDialog(
      <DialogContent>
        <DialogTitle>Move animal</DialogTitle>
      </DialogContent>,
    );

    await user.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("omits the corner close button when showCloseButton is false", async () => {
    const { dialog } = await openDialog(
      <DialogContent showCloseButton={false}>
        <DialogTitle>Move animal</DialogTitle>
      </DialogContent>,
    );

    expect(
      within(dialog).queryByRole("button", { name: "Close" }),
    ).not.toBeInTheDocument();
  });

  it("gives the footer no close button unless one is asked for", async () => {
    await openDialog(
      <DialogContent showCloseButton={false}>
        <DialogTitle>Move animal</DialogTitle>
        <DialogFooter>
          <Button>Move</Button>
        </DialogFooter>
      </DialogContent>,
    );

    const footer = slot("dialog-footer");
    expect(footer).toHaveClass(
      "flex",
      "flex-col-reverse",
      "border-t",
      "bg-muted/50",
      "sm:justify-end",
    );
    expect(within(footer).getByRole("button", { name: "Move" })).toBeVisible();
    expect(
      within(footer).queryByRole("button", { name: "Close" }),
    ).not.toBeInTheDocument();
  });

  it("adds a dismissing Close button to the footer on request", async () => {
    const { user } = await openDialog(
      <DialogContent showCloseButton={false}>
        <DialogTitle>Move animal</DialogTitle>
        <DialogFooter showCloseButton>
          <Button>Move</Button>
        </DialogFooter>
      </DialogContent>,
    );

    const close = within(slot("dialog-footer")).getByRole("button", {
      name: "Close",
    });
    expect(close).toHaveClass("border-border", "bg-background");

    await user.click(close);
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("dismisses from a composed DialogClose", async () => {
    const { user } = await openDialog(
      <DialogContent showCloseButton={false}>
        <DialogTitle>Move animal</DialogTitle>
        <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
      </DialogContent>,
    );

    const cancel = screen.getByRole("button", { name: "Cancel" });
    expect(cancel).toHaveAttribute("data-slot", "dialog-close");

    await user.click(cancel);
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});

describe("Tooltip", () => {
  it("shows a centred tooltip above the trigger on hover", async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger>Delete</TooltipTrigger>
          <TooltipContent>Removes the animal from the herd</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );

    await user.hover(screen.getByRole("button", { name: "Delete" }));

    const tip = await screen.findByText("Removes the animal from the herd");
    expect(tip).toHaveAttribute("data-slot", "tooltip-content");
    expect(tip).toHaveAttribute("data-side", "top");
    expect(tip).toHaveAttribute("data-align", "center");
    expect(tip).toHaveClass(
      "z-50",
      "rounded-md",
      "bg-foreground",
      "text-background",
      "text-xs",
    );
  });

  it("honours an explicit side, as the collapsed sidebar asks for", async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger>Animals</TooltipTrigger>
          <TooltipContent side="right">Animals</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );

    await user.hover(screen.getByRole("button", { name: "Animals" }));

    await waitFor(() => {
      expect(slot("tooltip-content")).toHaveAttribute("data-side", "right");
    });
  });
});

describe("Separator", () => {
  it("defaults to a horizontal rule and announces its orientation", () => {
    render(<Separator />);

    const separator = screen.getByRole("separator");
    expect(separator).toHaveAttribute("data-slot", "separator");
    expect(separator).toHaveAttribute("data-orientation", "horizontal");
    expect(separator).toHaveAttribute("aria-orientation", "horizontal");
    expect(separator).toHaveClass(
      "shrink-0",
      "bg-border",
      "data-horizontal:h-px",
      "data-vertical:w-px",
    );
  });

  it("switches to a vertical rule when asked", () => {
    render(<Separator orientation="vertical" className="mx-2" />);

    const separator = screen.getByRole("separator");
    expect(separator).toHaveAttribute("data-orientation", "vertical");
    expect(separator).toHaveAttribute("aria-orientation", "vertical");
    expect(separator).toHaveClass("mx-2");
  });
});

describe("Checkbox", () => {
  it("toggles from the keyboard and reports the new state", async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(<Checkbox name="create_animals" onCheckedChange={onCheckedChange} />);

    const box = screen.getByRole("checkbox");
    expect(box).toHaveAttribute("data-slot", "checkbox");
    expect(box).toHaveAttribute("aria-checked", "false");
    expect(box).toHaveClass(
      "peer",
      "size-4",
      "border-input",
      "data-checked:bg-primary",
    );
    // Unchecked, there is no tick to read out.
    expect(
      box.querySelector('[data-slot="checkbox-indicator"]'),
    ).not.toBeInTheDocument();

    await user.tab();
    expect(box).toHaveFocus();

    await user.keyboard(" ");
    expect(box).toHaveAttribute("aria-checked", "true");
    expect(onCheckedChange).toHaveBeenCalledTimes(1);
    expect(onCheckedChange.mock.calls[0]?.[0]).toBe(true);
    expect(
      box.querySelector('[data-slot="checkbox-indicator"]'),
    ).toBeInTheDocument();
  });

  it("stays put while disabled", async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(<Checkbox disabled onCheckedChange={onCheckedChange} />);

    await user.click(screen.getByRole("checkbox"));

    expect(screen.getByRole("checkbox")).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(onCheckedChange).not.toHaveBeenCalled();
  });
});

describe("Input, Textarea and Label", () => {
  it("wires a label to its input and takes typed text", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Label htmlFor="tag">Tag</Label>
        <Input id="tag" type="text" placeholder="G-001" />
      </>,
    );

    const input = screen.getByLabelText("Tag");
    expect(input).toHaveAttribute("data-slot", "input");
    expect(input).toHaveAttribute("type", "text");
    expect(input).toHaveClass(
      "h-9",
      "w-full",
      "rounded-lg",
      "border-input",
      "placeholder:text-muted-foreground",
      "disabled:cursor-not-allowed",
    );

    await user.type(input, "G-014");
    expect(input).toHaveValue("G-014");

    expect(screen.getByText("Tag")).toHaveClass(
      "flex",
      "items-center",
      "gap-2",
      "text-sm",
      "font-medium",
      "peer-disabled:opacity-50",
    );
  });

  it("gives the textarea a growing, bordered field", async () => {
    const user = userEvent.setup();
    render(<Textarea aria-label="Notes" placeholder="Anything unusual?" />);

    const notes = screen.getByLabelText("Notes");
    expect(notes).toHaveAttribute("data-slot", "textarea");
    expect(notes).toHaveClass(
      "field-sizing-content",
      "min-h-16",
      "w-full",
      "rounded-lg",
      "border-input",
    );

    await user.type(notes, "Limping on the left hind leg");
    expect(notes).toHaveValue("Limping on the left hind leg");
  });
});

describe("Skeleton", () => {
  it("renders a pulsing block that keeps the caller's dimensions", () => {
    render(<Skeleton className="h-4 w-24" />);

    expect(slot("skeleton")).toHaveClass(
      "animate-pulse",
      "rounded-md",
      "bg-muted",
      "h-4",
      "w-24",
    );
  });
});

describe("Toaster", () => {
  afterEach(() => {
    toast.dismiss();
  });

  it("resolves the system theme and stamps the project's toast class", async () => {
    render(<Toaster />);
    toast.success("Batch reviewed");

    const title = await screen.findByText("Batch reviewed");
    const item = title.closest("li");
    expect(item).toHaveClass("cn-toast");
    expect(item).toHaveAttribute("data-type", "success");

    const region = document.querySelector("[data-sonner-toaster]");
    expect(region).toHaveClass("toaster", "group");
    // "system" is sonner's cue to consult matchMedia; without it the toaster
    // would never follow the theme at all.
    expect(region).toHaveAttribute("data-sonner-theme", "light");
    expect(region).toHaveStyle({ "--normal-bg": "var(--popover)" });
  });

  it.each([
    ["success", () => toast.success("icon success"), "lucide-circle-check"],
    ["info", () => toast.info("icon info"), "lucide-info"],
    ["warning", () => toast.warning("icon warning"), "lucide-triangle-alert"],
    ["error", () => toast.error("icon error"), "lucide-octagon-x"],
  ])("shows the project's %s glyph", async (type, fire, glyph) => {
    render(<Toaster />);
    fire();

    const title = await screen.findByText(`icon ${type}`);
    const icon = title.closest("li")?.querySelector("[data-icon] svg");
    expect(icon).toHaveClass(glyph);
    // The size comes from this component, not from sonner's own glyph set.
    expect(icon).toHaveClass("size-4");
  });

  it("spins the loading glyph", async () => {
    render(<Toaster />);
    toast.loading("Saving…");

    const title = await screen.findByText("Saving…");
    const icon = title.closest("li")?.querySelector("[data-icon] svg");
    expect(icon).toHaveClass("lucide-loader-circle", "size-4", "animate-spin");
  });
});

describe("Providers", () => {
  it("supplies the query client, the auth session and the toaster", async () => {
    let defaults: DefaultOptions | undefined;

    function Probe() {
      defaults = useQueryClient().getDefaultOptions();
      const { loading, user } = useAuth();
      return <p>{loading ? "Loading…" : (user?.name ?? "signed out")}</p>;
    }

    render(
      <Providers>
        <Probe />
      </Providers>,
    );

    // The real AuthProvider bootstrap runs against the default MSW handlers.
    expect(await screen.findByText(TEST_USER.name)).toBeInTheDocument();

    // One retry and a 15 s stale window is what every page's queries inherit;
    // refetch-on-focus stays off so a tab switch never restorms the API.
    expect(defaults?.queries).toEqual({
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    });

    expect(
      screen.getByLabelText(/Notifications/, { selector: "section" }),
    ).toBeInTheDocument();
  });
});

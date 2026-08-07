import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Semantic badge for the status enums used across the API
 * (animals: ACTIVE/SOLD/DEAD/CULLED, kids: ALIVE/DIED/STILLBORN, …).
 * Accepts any string; unknown values fall back to a neutral badge.
 */

const TINTS = {
  emerald:
    "border-transparent bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  blue: "border-transparent bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  red: "border-transparent bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  amber:
    "border-transparent bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  neutral:
    "border-transparent bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
} as const;

const STATUS_TINTS: Record<string, keyof typeof TINTS> = {
  ACTIVE: "emerald",
  ALIVE: "emerald",
  BORN: "emerald",
  COMPLETED: "emerald",
  VERIFIED: "emerald",
  NORMAL: "emerald",
  SOLD: "blue",
  PURCHASED: "blue",
  CULLED: "red",
  STILLBORN: "red",
  REJECTED: "red",
  DEAD: "neutral",
  DIED: "neutral",
  QUARANTINE: "amber",
  PENDING: "amber",
  AWAITING_VERIFICATION: "amber",
  ASSISTED: "amber",
  DIFFICULT: "red",
};

function normalize(status: string) {
  return status.trim().toUpperCase().replace(/[\s-]+/g, "_");
}

function humanize(status: string) {
  return normalize(status)
    .toLowerCase()
    .replace(/(^|_)(\w)/g, (_, sep: string, c: string) =>
      (sep === "_" ? " " : "") + c.toUpperCase(),
    );
}

export function StatusBadge({
  status,
  className,
  children,
  ...props
}: {
  status: string;
  children?: React.ReactNode;
} & Omit<React.ComponentProps<typeof Badge>, "children" | "variant">) {
  const tint = STATUS_TINTS[normalize(status)];
  return (
    <Badge
      variant={tint ? "outline" : "secondary"}
      className={cn(tint && TINTS[tint], className)}
      {...props}
    >
      {children ?? humanize(status)}
    </Badge>
  );
}

import { cva, type VariantProps } from "class-variance-authority";
import {
  TrendingDown,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const statIconVariants = cva(
  "flex size-9 shrink-0 items-center justify-center rounded-lg [&_svg]:size-4.5",
  {
    variants: {
      tint: {
        default: "bg-muted text-muted-foreground",
        emerald:
          "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400",
        amber:
          "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400",
        red: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400",
      },
    },
    defaultVariants: {
      tint: "default",
    },
  },
);

const trendToneClasses = {
  positive: "text-emerald-600 dark:text-emerald-400",
  negative: "text-red-600 dark:text-red-400",
  neutral: "text-muted-foreground",
} as const;

export function StatCard({
  label,
  value,
  icon: Icon,
  hint,
  trend,
  tint = "default",
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  icon: LucideIcon;
  hint?: ReactNode;
  trend?: {
    value: ReactNode;
    direction: "up" | "down";
    tone?: keyof typeof trendToneClasses;
  };
  className?: string;
} & VariantProps<typeof statIconVariants>) {
  const hasHint = Boolean(hint) || hint === 0;
  const TrendIcon: LucideIcon | null = trend
    ? trend.direction === "up"
      ? TrendingUp
      : TrendingDown
    : null;
  return (
    <Card className={className}>
      <CardContent className="flex items-start gap-3">
        <span className={cn(statIconVariants({ tint }))}>
          <Icon />
        </span>
        <div className="min-w-0 space-y-1">
          <p className="text-sm leading-tight text-muted-foreground">{label}</p>
          <p className="text-2xl font-semibold tracking-normal">{value}</p>
          {(trend || hasHint) && (
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              {trend && TrendIcon && (
                <span
                  className={cn(
                    "inline-flex items-center gap-1 font-medium",
                    trendToneClasses[trend.tone ?? "neutral"],
                  )}
                >
                  <TrendIcon className="size-3.5" />
                  {trend.value}
                </span>
              )}
              {hint}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

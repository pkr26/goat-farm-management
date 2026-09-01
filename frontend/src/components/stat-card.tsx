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
  "flex size-10 shrink-0 items-center justify-center rounded-xl [&_svg]:size-[18px]",
  {
    variants: {
      tint: {
        default: "bg-muted text-muted-foreground",
        success:
          "bg-success-tint text-success-tint-foreground",
        warning: "bg-warning-tint text-warning-tint-foreground",
        info: "bg-info-tint text-info-tint-foreground",
        destructive: "bg-destructive/10 text-destructive",
      },
    },
    defaultVariants: {
      tint: "default",
    },
  },
);

const trendToneClasses = {
  positive: "text-success",
  negative: "text-destructive",
  neutral: "text-muted-foreground",
} as const;

export function StatCard({
  label,
  value,
  icon: Icon,
  hint,
  trend,
  footer,
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
  /** Optional dense visual (sparkline, mini-bars) rendered under the value. */
  footer?: ReactNode;
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
      <CardContent className="flex items-start gap-3.5">
        <span className={cn(statIconVariants({ tint }))}>
          <Icon aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-sm leading-tight text-muted-foreground">{label}</p>
          <p className="table-numeric font-heading text-2xl leading-tight font-semibold tracking-normal">
            {value}
          </p>
          {(trend || hasHint) && (
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              {trend && TrendIcon && (
                <span
                  className={cn(
                    "inline-flex items-center gap-1 font-medium",
                    trendToneClasses[trend.tone ?? "neutral"],
                  )}
                >
                  <TrendIcon className="size-3.5" aria-hidden="true" />
                  {trend.value}
                </span>
              )}
              {hint}
            </p>
          )}
          {footer}
        </div>
      </CardContent>
    </Card>
  );
}

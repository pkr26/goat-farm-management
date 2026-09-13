"use client";

// Stryker disable next-line StringLiteral: a module-level initializer cannot be attributed to the asserting test by per-test coverage; the chip styling is pinned by the campaign suite
const CHIP_CLASS = "h-7 rounded-md px-2 text-xs font-semibold";

/**
 * EN | తెలుగు segmented toggle. Lives next to the theme toggle in the app
 * shell header and on the login page. Each option is its own button with
 * aria-pressed so screen readers announce the current language as state,
 * not as a navigation target.
 */

import { Button } from "@/components/ui/button";
import { useLanguage } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const OPTIONS = [
  { code: "en", label: "EN" },
  { code: "te", label: "తెలుగు" },
] as const;

export function LanguageToggle({ className }: { className?: string }) {
  const { language, setLanguage } = useLanguage();

  return (
    <div
      role="group"
      aria-label="Language / భాష"
      className={cn(
        "flex items-center gap-0.5 rounded-lg border bg-card p-0.5",
        className,
      )}
    >
      {OPTIONS.map((option) => {
        const active = language === option.code;
        return (
          <Button
            key={option.code}
            type="button"
            variant="ghost"
            size="xs"
            aria-pressed={active}
            className={cn(
              CHIP_CLASS,
              active && "bg-primary/10 text-primary",
            )}
            onClick={() => setLanguage(option.code)}
          >
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}

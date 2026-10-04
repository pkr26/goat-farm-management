"use client";

import Link from "next/link";

import { useT, type MessageKey } from "@/lib/i18n";

/** A section tab: labels come from the message catalog, keyed by route
 * (label-string matching broke silently on copy edits — L20). */
export interface SectionNavTab<TabKey extends string> {
  href: string;
  key: TabKey;
  labelKey: MessageKey;
}

export type FeedingTab = "plan" | "recipes" | "inventory";
export type FinanceTab = "ledger" | "insurance";

export const FEEDING_TABS: SectionNavTab<FeedingTab>[] = [
  { href: "/feeding", key: "plan", labelKey: "feeding.nav.today" },
  { href: "/feeding/recipes", key: "recipes", labelKey: "feeding.nav.recipes" },
  { href: "/feeding/inventory", key: "inventory", labelKey: "feeding.nav.inventory" },
];

export const FINANCE_TABS: SectionNavTab<FinanceTab>[] = [
  { href: "/finance", key: "ledger", labelKey: "finance.nav.ledger" },
  { href: "/finance/insurance", key: "insurance", labelKey: "finance.nav.insurance" },
];

/**
 * Shared feeding and finance section navigation. Labels and the navigation landmark
 * name resolve through the active language catalog.
 */
export function SectionNav<TabKey extends string>({
  tabs,
  active,
  ariaLabelKey,
}: {
  tabs: SectionNavTab<TabKey>[];
  active: TabKey;
  /** Catalog key for the nav landmark's aria-label. */
  ariaLabelKey: MessageKey;
}) {
  const t = useT();
  return (
    <nav className="flex flex-wrap gap-1 border-b text-sm" aria-label={t(ariaLabelKey)}>
      {tabs.map((tab) => (
        <Link
          key={tab.key}
          href={tab.href}
          aria-current={tab.key === active ? "page" : undefined}
          className={
            tab.key === active
              ? "-mb-px border-b-2 border-primary px-3 py-2 font-medium text-foreground"
              : "-mb-px border-b-2 border-transparent px-3 py-2 text-muted-foreground hover:text-foreground"
          }
        >
          {t(tab.labelKey)}
        </Link>
      ))}
    </nav>
  );
}

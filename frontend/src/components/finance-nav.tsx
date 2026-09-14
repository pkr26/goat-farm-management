"use client";

import Link from "next/link";

export type FinanceTab = "ledger" | "insurance";

const FINANCE_TABS: { href: string; key: FinanceTab; label: string }[] = [
  { href: "/finance", key: "ledger", label: "Ledger" },
  { href: "/finance/insurance", key: "insurance", label: "Insurance" },
];

/** Section navigation shared by the finance pages, keyed by route (the
 *  feeding nav's precedent — label-string matching broke silently on copy
 *  edits, keys cannot). */
export function FinanceNav({ active }: { active: FinanceTab }) {
  return (
    <nav className="flex flex-wrap gap-1 border-b text-sm" aria-label="Finance sections">
      {FINANCE_TABS.map((tab) => (
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
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}

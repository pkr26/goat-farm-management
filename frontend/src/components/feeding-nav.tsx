"use client";

import Link from "next/link";

export type FeedingTab = "plan" | "recipes" | "inventory";

const FEEDING_TABS: { href: string; key: FeedingTab; label: string }[] = [
  { href: "/feeding", key: "plan", label: "Today's plan" },
  { href: "/feeding/recipes", key: "recipes", label: "Recipes" },
  { href: "/feeding/inventory", key: "inventory", label: "Inventory" },
];

/** Section navigation shared by the three feeding pages, keyed by route
 * (label-string matching broke silently on copy edits — L20). */
export function FeedingNav({ active }: { active: FeedingTab }) {
  return (
    <nav className="flex flex-wrap gap-1 border-b text-sm" aria-label="Feeding sections">
      {FEEDING_TABS.map((tab) => (
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

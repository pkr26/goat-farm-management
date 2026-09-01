"use client"

import * as React from "react"
import { ArrowDown, ArrowDownUp, ArrowUp } from "lucide-react"

import { cn } from "@/lib/utils"

function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div
      data-slot="table-container"
      className="relative w-full overflow-x-auto"
    >
      <table
        data-slot="table"
        className={cn("table-numeric w-full caption-bottom text-sm", className)}
        {...props}
      />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      className={cn("[&_tr]:border-b", className)}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t bg-muted/50 font-medium [&>tr]:last:border-b-0",
        className
      )}
      {...props}
    />
  )
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b transition-colors hover:bg-muted/50 has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted",
        className
      )}
      {...props}
    />
  )
}

function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "h-11 px-2 text-left align-middle font-medium whitespace-nowrap text-muted-foreground [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "p-2 align-middle whitespace-nowrap [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export type SortDirection = "asc" | "desc"

/**
 * A TableHead that sorts its column. Renders a full-width button with the
 * proper aria-sort on the surrounding th; pass the active direction from the
 * page's sort state. Clicking cycles asc → desc (the only two orders the
 * list endpoints' recency defaults need to restore).
 */
function SortableTableHead({
  column,
  label,
  direction,
  onSort,
  className,
  ...props
}: {
  /** Stable key the page uses to identify the column. */
  column: string
  label: React.ReactNode
  /** Current direction for THIS column; omit/null when another column (or no
   * column) is active — renders the neutral two-arrow icon. */
  direction?: SortDirection | null
  onSort: (column: string) => void
} & React.ComponentProps<"th">) {
  const ariaSort =
    direction === "asc" ? "ascending" : direction === "desc" ? "descending" : "none"
  const Icon = direction === "asc" ? ArrowUp : direction === "desc" ? ArrowDown : ArrowDownUp
  return (
    <TableHead
      aria-sort={ariaSort}
      className={cn("[&>button]:focus-visible:ring-[3px] [&>button]:focus-visible:ring-ring/50 [&>button]:focus-visible:outline-none", className)}
      {...props}
    >
      <button
        type="button"
        onClick={() => onSort(column)}
        className="inline-flex h-9 items-center gap-1 rounded text-left font-medium text-inherit transition-colors hover:text-foreground"
      >
        {label}
        <Icon aria-hidden="true" className="size-3.5 opacity-60" />
      </button>
    </TableHead>
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
  SortableTableHead,
}

"use client";

import { type KeyboardEvent, useEffect, useMemo, useState } from "react";
import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

export interface RemotePickerOption {
  value: string;
  label: string;
  description?: string;
}

export interface RemotePickerPage {
  /** Options remaining after any domain-specific eligibility filtering. */
  options: RemotePickerOption[];
  /** Total source records matching the server query, before eligibility filtering. */
  total: number;
  /** Source offset for the next request. This must advance even when options is empty. */
  nextOffset: number;
}

export interface RemotePickerLoadArgs {
  query: string;
  offset: number;
  limit: number;
  signal: AbortSignal;
}

interface RemotePickerProps {
  id: string;
  value: string;
  onValueChange: (value: string) => void;
  onOptionChange?: (option: RemotePickerOption) => void;
  /** Supplies a useful label for a prefilled selection outside loaded pages. */
  selectedOption?: RemotePickerOption | null;
  staticOptions?: RemotePickerOption[];
  placeholder: string;
  dialogTitle: string;
  dialogDescription?: string;
  searchLabel: string;
  searchPlaceholder?: string;
  searchMaxLength?: number;
  emptyMessage?: string;
  noEligibleYetMessage?: string;
  sourcePath: string;
  cacheKey: readonly unknown[];
  loadPage: (args: RemotePickerLoadArgs) => Promise<RemotePickerPage>;
  pageSize?: number;
  disabled?: boolean;
  className?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

const MIN_PAGE_SIZE = 10;
const MAX_PAGE_SIZE = 100;
/** Matches the animals list page, so every search box in the app settles at
 * the same pace. */
const SEARCH_DEBOUNCE_MS = 300;

/**
 * Accessible remote-data picker. Results are fetched only while its dialog is
 * open, searched on the server, and advanced by source offset so filtered
 * pages with zero eligible options can still reach later records.
 */
export function RemotePicker({
  id,
  value,
  onValueChange,
  onOptionChange,
  selectedOption,
  staticOptions = [],
  placeholder,
  dialogTitle,
  dialogDescription,
  searchLabel,
  searchPlaceholder = "Search…",
  searchMaxLength,
  emptyMessage = "No matching options.",
  noEligibleYetMessage = "No eligible options in the records checked yet.",
  sourcePath,
  cacheKey,
  loadPage,
  pageSize = 50,
  disabled = false,
  className,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: RemotePickerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [chosenOption, setChosenOption] = useState<RemotePickerOption | null>(null);
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const boundedPageSize = Math.min(MAX_PAGE_SIZE, Math.max(MIN_PAGE_SIZE, pageSize));

  // The query key is the search term, so an undebounced term would mint a new
  // key — and a new request — on every keystroke. The input itself stays
  // instant.
  useEffect(() => {
    const trimmed = search.trim();
    const handle = setTimeout(() => setDebouncedSearch(trimmed), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [search]);

  const results = useInfiniteQuery({
    queryKey: [sourcePath, "remote-picker", ...cacheKey, debouncedSearch, boundedPageSize],
    queryFn: ({ pageParam, signal }) =>
      loadPage({
        query: debouncedSearch,
        offset: pageParam,
        limit: boundedPageSize,
        signal,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, _pages, lastPageParam) =>
      lastPage.nextOffset > lastPageParam && lastPage.nextOffset < lastPage.total
        ? lastPage.nextOffset
        : undefined,
    enabled: open && !disabled,
    // A new search term is a new key with no cached data, which would unmount
    // the whole listbox mid-typing. Keep the previous result set rendered
    // (marked busy) until the next one arrives.
    placeholderData: keepPreviousData,
    retry: false,
  });

  const remoteOptions = useMemo(() => {
    const seen = new Set<string>();
    const options: RemotePickerOption[] = [];
    for (const page of results.data?.pages ?? []) {
      for (const option of page.options) {
        if (!seen.has(option.value)) {
          seen.add(option.value);
          options.push(option);
        }
      }
    }
    return options;
  }, [results.data?.pages]);

  const displayedOptions = useMemo(() => {
    const remoteValues = new Set(remoteOptions.map((option) => option.value));
    return [...staticOptions.filter((option) => !remoteValues.has(option.value)), ...remoteOptions];
  }, [remoteOptions, staticOptions]);

  const currentOption =
    displayedOptions.find((option) => option.value === value) ??
    (selectedOption?.value === value ? selectedOption : null) ??
    (chosenOption?.value === value ? chosenOption : null);
  const lastPage = results.data?.pages.at(-1);
  const checkedCount = lastPage?.nextOffset ?? 0;
  const total = lastPage?.total ?? 0;
  const hasMore = Boolean(results.hasNextPage);
  const statusId = `${id}-picker-status`;
  const searchId = `${id}-picker-search`;
  const dialogId = `${id}-picker-dialog`;
  const listboxId = `${id}-picker-results`;
  const valueId = `${id}-picker-value`;
  const selectedDisplayedIndex = displayedOptions.findIndex((option) => option.value === value);
  const tabbableOptionIndex = selectedDisplayedIndex >= 0 ? selectedDisplayedIndex : 0;

  function changeOpen(nextOpen: boolean) {
    setOpen(nextOpen);
    if (!nextOpen) {
      // Clear both halves together so reopening within the debounce window
      // does not query the previous term behind an empty input.
      setSearch("");
      setDebouncedSearch("");
    }
  }

  function choose(option: RemotePickerOption) {
    setChosenOption(option);
    onOptionChange?.(option);
    onValueChange(option.value);
    changeOpen(false);
  }

  function focusFirstOption() {
    document
      .getElementById(listboxId)
      ?.querySelector<HTMLElement>("[role='option']")
      ?.focus();
  }

  function moveOptionFocus(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const options = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>("[role='option']"),
    );
    if (options.length === 0) return;
    event.preventDefault();
    const currentIndex = options.indexOf(document.activeElement as HTMLElement);
    let nextIndex: number;
    if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = options.length - 1;
    else if (event.key === "ArrowUp") {
      nextIndex = currentIndex <= 0 ? options.length - 1 : currentIndex - 1;
    } else {
      nextIndex = currentIndex < 0 || currentIndex === options.length - 1 ? 0 : currentIndex + 1;
    }
    options.forEach((option, index) => {
      option.tabIndex = index === nextIndex ? 0 : -1;
    });
    options[nextIndex]?.focus();
  }

  return (
    <>
      <Button
        id={id}
        type="button"
        variant="outline"
        className={cn("w-full justify-between font-normal", className)}
        disabled={disabled}
        role="combobox"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={dialogId}
        aria-autocomplete="list"
        aria-invalid={ariaInvalid || undefined}
        aria-describedby={[ariaDescribedBy, valueId].filter(Boolean).join(" ")}
        onClick={() => changeOpen(true)}
      >
        <span
          id={valueId}
          className={cn("min-w-0 truncate", !currentOption && "text-muted-foreground")}
        >
          {currentOption?.label ?? (value ? `Selected item ${value}` : placeholder)}
        </span>
        <ChevronsUpDown className="text-muted-foreground" />
      </Button>

      <Dialog open={open} onOpenChange={changeOpen}>
        <DialogContent id={dialogId} className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{dialogTitle}</DialogTitle>
            <DialogDescription>
              {dialogDescription ?? "Search the farm records, then choose one option."}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1.5">
            <Label htmlFor={searchId}>{searchLabel}</Label>
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id={searchId}
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown") {
                    event.preventDefault();
                    focusFirstOption();
                  }
                }}
                placeholder={searchPlaceholder}
                maxLength={searchMaxLength}
                className="pl-8"
                aria-describedby={statusId}
                autoComplete="off"
              />
            </div>
          </div>

          <div
            id={listboxId}
            role="listbox"
            aria-label={`${dialogTitle} results`}
            aria-busy={results.isPlaceholderData || undefined}
            className={cn(
              "max-h-72 overflow-y-auto rounded-lg border p-1",
              results.isPlaceholderData && "opacity-60",
            )}
            onKeyDown={moveOptionFocus}
          >
            {displayedOptions.map((option, index) => (
              <OptionButton
                key={option.value}
                id={`${listboxId}-option-${index}`}
                option={option}
                selected={option.value === value}
                tabIndex={index === tabbableOptionIndex ? 0 : -1}
                onChoose={choose}
              />
            ))}

            {results.isPending ? (
              <p role="status" className="p-3 text-sm text-muted-foreground">
                Loading options…
              </p>
            ) : results.isError && !results.data ? (
              <div className="space-y-2 p-3">
                <p role="alert" className="text-sm text-destructive">
                  Could not load options.
                </p>
                <Button type="button" size="sm" variant="outline" onClick={() => void results.refetch()}>
                  Try again
                </Button>
              </div>
            ) : displayedOptions.length === 0 ? (
              // Gate on the combined list, not just the server results: static
              // options ("— none —") stay rendered and selectable above, so
              // claiming "no matching options" beside one would contradict
              // what the user sees.
              <p className="p-3 text-sm text-muted-foreground">
                {hasMore ? noEligibleYetMessage : emptyMessage}
              </p>
            ) : null}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <p id={statusId} aria-live="polite" className="text-xs text-muted-foreground">
              {results.data
                ? // Count what is actually selectable (static options included);
                  // "Checked X of Y" still describes the server records only.
                  `${displayedOptions.length} option${displayedOptions.length === 1 ? "" : "s"} available. Checked ${checkedCount} of ${total} matching record${total === 1 ? "" : "s"}.${hasMore ? " More records are available." : " All matching records checked."}`
                : "Results load when this picker opens."}
            </p>
            {hasMore && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={results.isFetchingNextPage}
                onClick={() => void results.fetchNextPage()}
              >
                {results.isFetchingNextPage ? "Loading more…" : "Load more"}
              </Button>
            )}
          </div>
          {results.isFetchNextPageError && (
            <p role="alert" className="text-sm text-destructive">
              The next page could not be loaded. Try Load more again.
            </p>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

function OptionButton({
  id,
  option,
  selected,
  tabIndex,
  onChoose,
}: {
  id: string;
  option: RemotePickerOption;
  selected: boolean;
  tabIndex: number;
  onChoose: (option: RemotePickerOption) => void;
}) {
  return (
    <button
      type="button"
      role="option"
      id={id}
      tabIndex={tabIndex}
      className="flex w-full items-start gap-2 rounded-md px-2 py-2 text-left outline-none hover:bg-accent focus-visible:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
      aria-selected={selected}
      onClick={() => onChoose(option)}
    >
      <Check className={cn("mt-0.5 size-4 shrink-0", !selected && "invisible")} />
      <span className="min-w-0">
        <span className="block truncate text-sm">{option.label}</span>
        {option.description && (
          <span className="block text-xs text-muted-foreground">{option.description}</span>
        )}
      </span>
    </button>
  );
}

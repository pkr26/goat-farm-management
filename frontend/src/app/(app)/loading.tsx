import { PageSkeleton } from "@/components/skeletons";

/** Route-level loading fallback for the app shell — mirrors the page
 *  layout (stat row + content cards) so navigation feels continuous. */
export default function Loading() {
  return (
    <div role="status" aria-live="polite" aria-label="Loading page">
      <span className="sr-only">Loading…</span>
      <PageSkeleton stats={4} cards={2} />
    </div>
  );
}

/** Route-level loading fallback for the app shell — same placeholder the
 *  pages render while their queries are in flight. */
export default function Loading() {
  return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
}

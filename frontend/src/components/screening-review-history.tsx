"use client";

import { useState } from "react";
import { useFindingReviewHistoryApiScreeningFindingsFindingIdReviewsGet } from "@/api/generated/endpoints";
import { PaginationControls } from "@/components/pagination-controls";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { formatFarmDateTime } from "@/lib/format";
import { useEnumLabel } from "@/lib/enum-labels";

export function ScreeningReviewHistory({ findingId }: { findingId: number }) {
  const t = useT();
  const enumLabel = useEnumLabel();
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const query = useFindingReviewHistoryApiScreeningFindingsFindingIdReviewsGet(
    findingId, { limit: 10, offset }, { query: { enabled: open } },
  );
  const history = query.data?.status === 200 ? query.data.data : undefined;
  return <details className="mt-2" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer font-medium">{t("screening.review.history")}</summary>
    {open && query.isPending ? <p role="status">{t("common.loading")}</p> : null}
    {open && query.isError ? <div role="alert">
      <p>{t("common.somethingWentWrong")}</p>
      <Button type="button" variant="outline" onClick={() => void query.refetch()}>{t("common.retry")}</Button>
    </div> : null}
    {open && history ? <>
      {history.legacy_review ? <p>{t("screening.review.legacy")}</p> : null}
      {!history.total ? <p>{t("screening.review.noHistory")}</p> : null}
      <ol className="mt-2 space-y-2">
        {history.reviews.map((review) => <li key={review.revision} className="rounded border p-2">
          <p>{t("screening.review.historyEntry", { revision: review.revision,
            author: review.reviewed_by_id, date: formatFarmDateTime(review.reviewed_at) })}</p>
          <StatusBadge status={review.status}>
            {enumLabel("screeningReviewStatus", review.status)}
          </StatusBadge>
          {review.review_note ? <p className="mt-1">{review.review_note}</p> : null}
        </li>)}
      </ol>
      <PaginationControls total={history.total} offset={history.offset} limit={history.limit}
        onOffsetChange={setOffset} label={t("screening.review.history")} />
    </> : null}
  </details>;
}

"use client";

/**
 * Photo-screening review queue — the disease-detection pipeline's human
 * half. The worker runs the daily photos through the rotating gate model
 * (plus specialists and a second-model cross-check on flagged photos);
 * this page is where a vet works down the findings and confirms/rejects
 * them into the training corpus.
 */

import { Camera, ChevronLeft, Download, Stethoscope } from "lucide-react";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import {
  exportDatasetApiScreeningExportGet,
  useGetImageApiScreeningImagesImageIdGet,
  useListImagesApiScreeningImagesGet,
  useProviderStatsApiScreeningStatsGet,
  useReviewFindingApiScreeningFindingsFindingIdReviewPost,
} from "@/api/generated/endpoints";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PaginationControls } from "@/components/pagination-controls";
import { PermissionGate } from "@/components/permission-gate";
import { PageSkeleton, TableSkeleton } from "@/components/skeletons";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DiseaseCheckDialog } from "@/components/screening-check-dialog";
import { formatDate } from "@/lib/format";
import { useT, type TFn } from "@/lib/i18n";
import { ApiError } from "@/lib/api-client";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { MAX_PAGE_OFFSET, useUrlState } from "@/lib/use-url-state";

const PAGE_LIMIT = 25;

const STATUS_FILTERS = ["ALL", "FLAGGED", "HEALTHY", "ERROR"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

const FILTER_KEYS: Record<StatusFilter, Parameters<TFn>[0]> = {
  ALL: "screening.filter.all",
  FLAGGED: "screening.filter.flagged",
  HEALTHY: "screening.filter.healthy",
  ERROR: "screening.filter.errors",
};

// t() is keyed by MessageKey literals, so dynamic status strings resolve
// through these tables; an unexpected value falls back to the raw code.
const IMAGE_STATUS_KEYS = {
  PENDING: "screening.status.PENDING",
  PROCESSING: "screening.status.PROCESSING",
  HEALTHY: "screening.status.HEALTHY",
  FLAGGED: "screening.status.FLAGGED",
  SKIPPED: "screening.status.SKIPPED",
  ERROR: "screening.status.ERROR",
} as const;

const FINDING_STATUS_KEYS = {
  PENDING_REVIEW: "screening.finding.PENDING_REVIEW",
  CONFIRMED: "screening.finding.CONFIRMED",
  REJECTED: "screening.finding.REJECTED",
} as const;

const SEVERITY_KEYS = {
  mild: "screening.severity.mild",
  moderate: "screening.severity.moderate",
  severe: "screening.severity.severe",
} as const;

function severityLabel(t: TFn, severity: string | null | undefined): string | null {
  if (!severity) return null;
  const key = SEVERITY_KEYS[severity as keyof typeof SEVERITY_KEYS];
  return key ? t(key) : severity;
}

function imageStatusLabel(t: TFn, status: string): string {
  const key = IMAGE_STATUS_KEYS[status as keyof typeof IMAGE_STATUS_KEYS];
  return key ? t(key) : status;
}

function findingStatusLabel(t: TFn, status: string): string {
  const key = FINDING_STATUS_KEYS[status as keyof typeof FINDING_STATUS_KEYS];
  return key ? t(key) : status;
}

function confidenceLabel(
  t: TFn,
  value: number | string | null | undefined,
): string {
  if (value === null || value === undefined) return "—";
  const numeric = typeof value === "number" ? value : Number.parseFloat(value);
  if (!Number.isFinite(numeric)) return "—";
  return `${t("screening.detail.confidence")} ${Math.round(numeric * 100)}%`;
}

function ScreeningPageContent({ perms }: { perms: PermissionsState }) {
  const t = useT();
  const allowed = perms.can("health.view");
  const { get, getNumber, set: setUrlState } = useUrlState();

  const offset = getNumber("offset", 0, 0, MAX_PAGE_OFFSET);
  const statusParam = get("status");
  const filter: StatusFilter = (STATUS_FILTERS as readonly string[]).includes(statusParam ?? "")
    ? (statusParam as StatusFilter)
    : "ALL";
  const selectedImageId = getNumber("image_id", 0, 0, Number.MAX_SAFE_INTEGER) || null;

  const listQuery = useListImagesApiScreeningImagesGet(
    {
      limit: PAGE_LIMIT,
      offset,
      status: filter === "ALL" ? undefined : filter,
    },
    {
      query: {
        enabled: allowed,
        placeholderData: (previous) => previous,
      },
    },
  );
  const payload = listQuery.data?.status === 200 ? listQuery.data.data : undefined;

  const canManage = perms.can("health.manage");

  const statsQuery = useProviderStatsApiScreeningStatsGet(
    { days: 30 },
    { query: { enabled: allowed } },
  );
  const stats = statsQuery.data?.status === 200 ? statsQuery.data.data : undefined;

  const [checkOpen, setCheckOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const downloadDataset = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const result = await exportDatasetApiScreeningExportGet({ vet_status: "ALL" });
      if (result.status !== 200) return;
      const blob = new Blob([JSON.stringify(result.data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `screening-dataset-${new Date().toISOString().slice(0, 10)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success(t("screening.export.done", { count: result.data.record_count }));
    } catch {
      toast.error(t("common.somethingWentWrong"));
    } finally {
      setExporting(false);
    }
  };

  const detailQuery = useGetImageApiScreeningImagesImageIdGet(selectedImageId ?? 0, {
    query: { enabled: allowed && selectedImageId !== null },
  });
  const detail = detailQuery.data?.status === 200 ? detailQuery.data.data : undefined;
  const cropIndexById = new Map<number, number>(
    (detail?.crops ?? [])
      .filter((crop) => crop.id !== undefined && crop.crop_index !== undefined)
      .map((crop) => [crop.id as number, crop.crop_index as number]),
  );

  const reviewMutation = useReviewFindingApiScreeningFindingsFindingIdReviewPost();
  const submitReview = async (
    findingId: number,
    status: "CONFIRMED" | "REJECTED",
    expectedStatus: "PENDING_REVIEW" | "CONFIRMED" | "REJECTED",
  ) => {
    try {
      await reviewMutation.mutateAsync({
        findingId,
        data: { status, expected_status: expectedStatus },
      });
      toast.success(t("screening.review.reviewed"));
      await Promise.all([detailQuery.refetch(), listQuery.refetch()]);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        toast.error(t("screening.review.conflict"));
      } else {
        toast.error(t("common.somethingWentWrong"));
      }
    }
  };

  if (!allowed) {
    return (
      <EmptyState
        icon={Camera}
        title={t("screening.empty.title")}
        description={t("screening.empty.description")}
      />
    );
  }

  const rows = payload?.images ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("screening.title")}
        description={t("screening.description")}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setCheckOpen(true)}>
              <Stethoscope aria-hidden /> {t("screening.check.button")}
            </Button>
            {canManage ? (
              <Button variant="outline" size="sm" disabled={exporting} onClick={downloadDataset}>
                <Download aria-hidden /> {t("screening.export.button")}
              </Button>
            ) : null}
          </div>
        }
      />
      <p className="text-sm text-muted-foreground">
        {t("screening.note.modelScreenNotDiagnosis")}
      </p>

      <DiseaseCheckDialog
        open={checkOpen}
        onOpenChange={setCheckOpen}
        onFinished={() => {
          void listQuery.refetch();
        }}
      />

      <DataTableCard
        title={t("screening.stats.title")}
        description={t("screening.stats.description", { days: 30 })}
      >
        {statsQuery.isPending ? (
          <div role="status" aria-live="polite">
            <TableSkeleton rows={2} columns={6} />
          </div>
        ) : !stats || stats.providers.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("screening.stats.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>{t("screening.stats.provider")}</th>
                  <th>{t("screening.stats.gateRuns")}</th>
                  <th>{t("screening.stats.flagRate")}</th>
                  <th>{t("screening.stats.errors")}</th>
                  <th>{t("screening.stats.latency")}</th>
                  <th>{t("screening.stats.agreement")}</th>
                  <th>{t("screening.stats.confirmed")}</th>
                  <th>{t("screening.stats.rejected")}</th>
                </tr>
              </thead>
              <tbody>
                {stats.providers.map((row) => {
                  const flagRate =
                    row.gate_runs > 0
                      ? `${Math.round((row.gate_flagged / row.gate_runs) * 100)}%`
                      : "—";
                  const agreement =
                    row.cross_checks > 0
                      ? `${Math.round((row.cross_check_agreements / row.cross_checks) * 100)}%`
                      : "—";
                  return (
                    <tr key={`${row.provider}/${row.model}`}>
                      <td>
                        {row.provider} <span className="text-muted-foreground">· {row.model}</span>
                      </td>
                      <td>{row.gate_runs}</td>
                      <td>{flagRate}</td>
                      <td>{row.gate_errors}</td>
                      <td>
                        {row.avg_gate_latency_ms === null
                          ? "—"
                          : `${(row.avg_gate_latency_ms / 1000).toFixed(1)}s`}
                      </td>
                      <td>{agreement}</td>
                      <td>{row.findings_confirmed}</td>
                      <td>{row.findings_rejected}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </DataTableCard>

      {selectedImageId !== null ? (
        <DataTableCard
          title={t("screening.detail.title")}
          actions={
            <Button
              variant="outline"
              size="sm"
              onClick={() => setUrlState({ image_id: null, offset: offset > 0 ? offset : null })}
            >
              <ChevronLeft aria-hidden /> {t("screening.detail.back")}
            </Button>
          }
        >
          {detailQuery.isPending ? (
            <div role="status" aria-live="polite">
              <TableSkeleton rows={3} columns={3} />
            </div>
          ) : detail ? (
            <div className="grid gap-6 md:grid-cols-2">
              <div className="space-y-2">
                {detail.image_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={detail.image_url}
                    alt={detail.s3_key}
                    className="max-h-[480px] w-full rounded-lg border object-contain"
                  />
                ) : (
                  <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
                    {t("screening.detail.imageUnavailable")}
                  </p>
                )}
                <dl className="grid grid-cols-2 gap-2 text-sm">
                  <dt className="text-muted-foreground">{t("screening.table.date")}</dt>
                  <dd>{formatDate(detail.captured_date)}</dd>
                  <dt className="text-muted-foreground">{t("screening.table.status")}</dt>
                  <dd>
                    <StatusBadge status={detail.status}>
                      {imageStatusLabel(t, detail.status)}
                    </StatusBadge>
                  </dd>
                </dl>
              </div>
              <div className="space-y-4">
                <section className="space-y-2">
                  <h3 className="font-medium">{t("screening.crops.title")}</h3>
                  {(detail.crops ?? []).length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {t("screening.crops.none")}
                    </p>
                  ) : (
                    <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                      {(detail.crops ?? []).map((crop) => (
                        <li key={crop.id} className="space-y-1 rounded-lg border p-2 text-sm">
                          {crop.image_url ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={crop.image_url}
                              alt={`${t("screening.crops.goat")} ${crop.crop_index + 1}`}
                              className="h-28 w-full rounded object-cover"
                            />
                          ) : null}
                          <div className="flex items-center justify-between gap-1">
                            <span className="font-medium">
                              {t("screening.crops.goat")} {crop.crop_index + 1}
                            </span>
                            <StatusBadge status={crop.status}>
                              {imageStatusLabel(t, crop.status)}
                            </StatusBadge>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
                <section className="space-y-2">
                  <h3 className="font-medium">{t("screening.detail.findings")}</h3>
                  {(detail.findings ?? []).length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {t("screening.detail.noFindings")}
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {(detail.findings ?? []).map((finding) => {
                        const severity = severityLabel(t, finding.severity);
                        const cropId = finding.crop_id ?? null;
                        const goatIndex =
                          cropId === null ? undefined : cropIndexById.get(cropId);
                        return (
                          <li key={finding.id} className="rounded-lg border p-3 text-sm">
                            <div className="flex flex-wrap items-center gap-2">
                              <StatusBadge status={finding.status}>
                                {findingStatusLabel(t, finding.status)}
                              </StatusBadge>
                              {goatIndex !== undefined ? (
                                <Badge variant="outline">
                                  {t("screening.crops.goat")} {goatIndex + 1}
                                </Badge>
                              ) : null}
                              {finding.region ? (
                                <Badge variant="outline">{finding.region}</Badge>
                              ) : null}
                              {severity ? <Badge variant="secondary">{severity}</Badge> : null}
                              <span className="font-medium">{finding.label}</span>
                              <span className="text-muted-foreground">
                                {confidenceLabel(t, finding.confidence)}
                              </span>
                            </div>
                            {finding.note ? (
                              <p className="mt-1 text-muted-foreground">{finding.note}</p>
                            ) : null}
                            {finding.review_note ? (
                              <p className="mt-1 text-muted-foreground">
                                {t("screening.review.reviewed")}: {finding.review_note}
                              </p>
                            ) : null}
                            {canManage ? (
                              <div className="mt-2 flex gap-2">
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={reviewMutation.isPending}
                                  onClick={() =>
                                    submitReview(finding.id, "CONFIRMED", finding.status)
                                  }
                                >
                                  {t("screening.review.confirm")}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={reviewMutation.isPending}
                                  onClick={() =>
                                    submitReview(finding.id, "REJECTED", finding.status)
                                  }
                                >
                                  {t("screening.review.reject")}
                                </Button>
                              </div>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
                <section className="space-y-2">
                  <h3 className="font-medium">{t("screening.detail.runs")}</h3>
                  <ul className="space-y-2 text-sm">
                    {(detail.runs ?? []).map((run) => {
                      const isCrossCheck = run.stage === "CROSS_CHECK";
                      const agrees = isCrossCheck ? run.detail?.agrees === true : null;
                      return (
                        <li key={run.id} className="rounded-lg border p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <StatusBadge status={run.run_status === "OK" ? "DONE" : "ERROR"}>
                              {run.run_status}
                            </StatusBadge>
                            <Badge variant="outline">{run.stage}</Badge>
                            {run.verdict ? <Badge variant="outline">{run.verdict}</Badge> : null}
                            <span>
                              {run.provider} · {run.model}
                            </span>
                            <span className="text-muted-foreground">
                              {confidenceLabel(t, run.confidence)}
                            </span>
                            {agrees !== null && run.run_status === "OK" ? (
                              <Badge variant={agrees ? "default" : "destructive"}>
                                {t(agrees ? "screening.detail.agrees" : "screening.detail.disagrees")}
                              </Badge>
                            ) : null}
                          </div>
                          {run.error ? <p className="mt-1 text-destructive">{run.error}</p> : null}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              </div>
            </div>
          ) : null}
        </DataTableCard>
      ) : (
        <DataTableCard
          title={t("screening.title")}
          actions={
            <div className="flex flex-wrap gap-1" role="group" aria-label={t("screening.filter.all")}>
              {STATUS_FILTERS.map((candidate) => (
                <Button
                  key={candidate}
                  size="sm"
                  variant={filter === candidate ? "default" : "outline"}
                  aria-pressed={filter === candidate}
                  onClick={() =>
                    setUrlState({
                      status: candidate === "ALL" ? null : candidate,
                      offset: null,
                    })
                  }
                >
                  {t(FILTER_KEYS[candidate])}
                </Button>
              ))}
            </div>
          }
          ariaBusy={listQuery.isPlaceholderData}
        >
          {listQuery.isPending ? (
            <div role="status" aria-live="polite">
              <TableSkeleton rows={8} columns={5} />
            </div>
          ) : !payload ? (
            <EmptyState
              icon={Camera}
              title={t("common.somethingWentWrong")}
              description={t("common.retry")}
            />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={Camera}
              title={t("screening.empty.title")}
              description={t("screening.empty.description")}
            />
          ) : (
            <>
              <div className="overflow-x-auto">
                <table>
                  <thead>
                    <tr>
                      <th>{t("screening.table.status")}</th>
                      <th>{t("screening.table.date")}</th>
                      <th className="hidden md:table-cell">{t("screening.table.photo")}</th>
                      <th>{t("screening.table.findings")}</th>
                      <th className="hidden lg:table-cell">{t("screening.table.model")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr
                        key={row.id}
                        className="cursor-pointer"
                        onClick={() => setUrlState({ image_id: String(row.id) })}
                      >
                        <td>
                          <StatusBadge status={row.status}>
                            {imageStatusLabel(t, row.status)}
                          </StatusBadge>
                        </td>
                        <td>{formatDate(row.captured_date)}</td>
                        <td className="hidden max-w-[240px] truncate text-muted-foreground md:table-cell">
                          {row.s3_key}
                        </td>
                        <td>
                          {(row.pending_findings ?? 0) > 0 ? (
                            <Badge variant="destructive">{row.pending_findings ?? 0}</Badge>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="hidden text-muted-foreground lg:table-cell">
                          {row.latest_run
                            ? `${row.latest_run.provider} · ${row.latest_run.model}`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <PaginationControls
                total={payload.total}
                limit={payload.limit}
                offset={payload.offset}
                onOffsetChange={(next) => setUrlState({ offset: next > 0 ? next : null })}
                label={t("screening.table.photo")}
                disabled={listQuery.isPlaceholderData}
              />
            </>
          )}
        </DataTableCard>
      )}
    </div>
  );
}

export default function ScreeningPage() {
  const perms = usePermissions();
  const t = useT();
  return (
    <Suspense
      fallback={
        <div className="space-y-6" role="status" aria-live="polite">
          <PageHeader
            title={t("screening.title")}
            description={t("screening.description")}
          />
          <PageSkeleton cards={2} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="health.view"
        label={t("screening.title")}
        description={t("screening.description")}
        cards={2}
      >
        <ScreeningPageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}

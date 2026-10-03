"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  useHealthRoundProgressApiHealthRoundsTaskIdGet,
  useStartHealthRoundApiHealthRoundsTaskIdStartPost,
  useAddHealthRoundTargetsApiHealthRoundsTaskIdTargetsPost,
  useExcludeHealthRoundTargetsApiHealthRoundsTaskIdExclusionsPost,
} from "@/api/generated/endpoints";
import { HealthAnimalPicker } from "@/components/health-target-pickers";
import { PaginationControls } from "@/components/pagination-controls";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/lib/i18n";
import { formatDate } from "@/lib/format";
import { ApiError } from "@/lib/api-client";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { mapServerError } from "@/lib/server-error-phrases";
import { invalidateFarmData } from "@/lib/query-invalidation";

/** The round certifies the declared cohort, with every component visible. */
export function HealthRoundProgress({ taskId, component, onComponentChange, onCohortChange }: {
  taskId: number;
  component: string;
  onComponentChange: (value: string) => void;
  onCohortChange: () => void;
}) {
  const t = useT();
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const queryClient = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<number[]>([]);
  const [arrival, setArrival] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const query = useHealthRoundProgressApiHealthRoundsTaskIdGet(taskId, { limit: 50, offset });
  const round = query.data?.status === 200 ? query.data.data : undefined;
  const start = useStartHealthRoundApiHealthRoundsTaskIdStartPost();
  const add = useAddHealthRoundTargetsApiHealthRoundsTaskIdTargetsPost();
  const exclude = useExcludeHealthRoundTargetsApiHealthRoundsTaskIdExclusionsPost();
  const busy = start.isPending || add.isPending || exclude.isPending;
  async function change(kind: "start" | "add" | "exclude") {
    if (busy) return;
    const stillOwnsFarm = captureFarmScope();
    setError(null);
    try {
      if (kind === "start") await start.mutateAsync({ taskId });
      else {
        const data = { animal_ids: kind === "add" ? [Number(arrival)] : selected, reason: reason.trim() };
        if (kind === "add") await add.mutateAsync({ taskId, data });
        else await exclude.mutateAsync({ taskId, data });
      }
      if (!stillOwnsFarm()) return;
      invalidateFarmData(queryClient);
      if (!mounted.current) return;
      setSelected([]);
      setArrival("");
      setReason("");
      onCohortChange();
      await query.refetch();
      toast.success(t("health.round.changed"));
    } catch (err) {
      if (stillOwnsFarm() && mounted.current) setError(err instanceof ApiError
        ? mapServerError(t, err.detail, err.status, err.code)
        : t("common.somethingWentWrong"));
    }
  }
  return (
    <section aria-label={t("health.round.title")} className="space-y-3 rounded-lg border p-3 text-sm">
      <h3 className="font-medium">{t("health.round.title")}</h3>
      <p className="text-muted-foreground">{t("health.round.explainer")}</p>
      {query.isPending ? <p role="status">{t("common.loading")}</p> : query.isError ? (
        <div role="alert"><p>{t("common.somethingWentWrong")}</p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>{t("common.retry")}</Button>
        </div>
      ) : round ? <>
        <p>{t("health.round.progress", { covered: round.covered_targets, total: round.total_targets,
          excluded: round.excluded_targets, remaining: round.remaining_units })}</p>
        {!round.initialized ? (
          <Button type="button" disabled={busy || round.task_status !== "PENDING"}
            onClick={() => void change("start")}>{t("health.round.start")}</Button>
        ) : <>
          <p className="text-xs text-muted-foreground">{t("health.round.snapshot", { date: formatDate(round.snapshot_at) })}</p>
          <Label htmlFor={`round-component-${taskId}`}>{t("health.round.component")}</Label>
          <select id={`round-component-${taskId}`} value={round.required_components.includes(component) ? component : ""}
            disabled={busy} onChange={(event) => onComponentChange(event.target.value)}
            className="h-10 w-full rounded-md border bg-background px-3">
            <option value="">{t("health.round.pickComponent")}</option>
            {round.required_components.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
          <ul className="max-h-52 space-y-2 overflow-y-auto">
            {round.targets.map((target) => <li key={target.animal_id} className="rounded border p-2">
              <label className="flex items-center gap-2">
                <input type="checkbox" aria-label={t("health.round.selectTarget", { tag: target.animal_tag })}
                  disabled={busy || Boolean(target.exclusion_reason) || round.task_status !== "PENDING"}
                  checked={selected.includes(target.animal_id)} onChange={(event) => setSelected((ids) =>
                    event.target.checked ? [...ids, target.animal_id] : ids.filter((id) => id !== target.animal_id))} />
                <span className="font-medium">{target.animal_tag}</span>
              </label>
              <p>{t("health.round.coverage")}: {target.covered_components.join(", ") || "—"}</p>
              {target.exclusion_reason ? <p>{t("health.round.exclusion")}: {target.exclusion_reason}</p> : null}
            </li>)}
          </ul>
          <PaginationControls total={round.total_targets} limit={round.limit} offset={round.offset}
            onOffsetChange={setOffset} label={t("health.form.reviewedListLabel")} />
          {round.task_status === "PENDING" ? <>
            <Label htmlFor={`round-arrival-${taskId}`}>{t("health.round.addAnimal")}</Label>
            <HealthAnimalPicker id={`round-arrival-${taskId}`} value={arrival} disabled={busy}
              onValueChange={setArrival} />
            <Label htmlFor={`round-reason-${taskId}`}>{t("health.round.reason")}</Label>
            <Input id={`round-reason-${taskId}`} value={reason} maxLength={2000} disabled={busy}
              onChange={(event) => setReason(event.target.value)} />
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" disabled={busy || !arrival || !reason.trim()}
                onClick={() => void change("add")}>{t("health.round.add")}</Button>
              <Button type="button" variant="outline" disabled={busy || !selected.length || !reason.trim()}
                onClick={() => void change("exclude")}>{t("health.round.exclude")}</Button>
            </div>
          </> : null}
        </>}
      </> : null}
      {error ? <p role="alert" className="text-destructive">{error}</p> : null}
    </section>
  );
}

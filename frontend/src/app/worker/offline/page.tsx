"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, WifiOff, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { useAuth } from "@/lib/auth-context";
import { endOfflineShift, readOfflineShift, type OfflineShift } from "@/lib/worker-offline-shift";
import { OutboxReviewRequiredError, persistOfflineWorkerDraft, readWorkerOutbox, type WorkerOperation } from "@/lib/worker-outbox";
import { formatFarmDateTime } from "@/lib/format";

/** A bounded snapshot for the ongoing shift. It grants no API authority:
 * every operation waits for real authentication by its original worker. */
export default function OfflineWorkerPage() {
  const t = useT();
  const router = useRouter();
  const { user, loading } = useAuth();
  const [snapshot, setSnapshot] = useState<OfflineShift | null>(null);
  const [ready, setReady] = useState(false);
  const [saved, setSaved] = useState<ReadonlyMap<number, WorkerOperation["state"]>>(new Map());
  const [busy, setBusy] = useState<ReadonlySet<number>>(new Set());
  const [storageError, setStorageError] = useState(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const shift = await readOfflineShift();
        if (!active) return;
        const records = shift ? await readWorkerOutbox(shift) : [];
        if (!active) return;
        setSnapshot(shift);
        setSaved(new Map(records.map((item) => [Number(item.path.match(/\/tasks\/(\d+)/)?.[1]), item.state])));
      } catch { if (active) setStorageError(true); }
      finally { if (active) setReady(true); }
    };
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    if (!loading && user !== null) router.replace("/worker");
  }, [loading, user, router]);

  async function record(id: number, kind: "complete" | "skip") {
    if (!snapshot || saved.has(id) || busy.has(id)) return;
    const duty = snapshot.tasks.find((task) => task.id === id);
    if (!duty || (kind === "complete" ? !duty.canComplete : !duty.canSkip)) return;
    setBusy((previous) => new Set(previous).add(id));
    try {
      // Recheck the active shift after every asynchronous boundary. An end
      // shift in this tab must close the local recording capability too.
      const current = await readOfflineShift();
      if (!current || current.actorScope !== snapshot.actorScope || current.farmScope !== snapshot.farmScope ||
        current.expiresAt !== snapshot.expiresAt) { setSnapshot(null); return; }
      await persistOfflineWorkerDraft(`/api/tasks/${id}/${kind}`,
        kind === "skip" ? JSON.stringify({ reason: t("worker.skipReason") }) : undefined, snapshot);
      setSaved((previous) => new Map(previous).set(id, "review"));
      toast.info(t("worker.offlineDraftSaved"));
    } catch (error) { setStorageError(true); toast.error(t(error instanceof OutboxReviewRequiredError ? "worker.receipts.review" : "worker.queueFull")); }
    finally { setBusy((previous) => { const next = new Set(previous); next.delete(id); return next; }); }
  }

  async function endShift() {
    try { await endOfflineShift(); setSnapshot(null); router.replace("/worker/login"); }
    catch { setStorageError(true); }
  }

  if (!ready || loading) return <p role="status" className="p-6">{t("common.loading")}</p>;
  return <div className="mx-auto max-w-3xl space-y-5 p-4">
    <h1 className="flex items-center gap-2 text-2xl font-semibold"><WifiOff aria-hidden />{t("worker.offlineShift.title")}</h1>
    <p className="text-muted-foreground">{t("worker.offlineShift.description")}</p>
    {storageError && <p role="alert" className="text-destructive">{t("worker.queueFull")}</p>}
    {snapshot ? <>
      <p className="font-semibold">{snapshot.farmName} · {snapshot.workerName}</p>
      <p className="text-sm text-muted-foreground">{t("worker.offlineShift.checked", { date: formatFarmDateTime(new Date(snapshot.verifiedAt).toISOString()) })}</p>
      <ul className="space-y-3">
        {snapshot.tasks.map((task) => <li key={task.id} data-testid={`offline-duty-${task.id}`} className="rounded-lg border bg-card p-4">
          <h2 className="font-semibold">{task.title}</h2>
          <p className="text-sm text-muted-foreground">{task.dueDate}</p>
          {saved.has(task.id) ? <p role="status" className="mt-2">{t(`worker.receipts.${saved.get(task.id)!}`)}</p> :
            task.canComplete ? <div className="mt-3 flex gap-3">
              <Button data-testid={`offline-complete-${task.id}`} className="h-11" disabled={busy.has(task.id)} onClick={() => void record(task.id, "complete")}><Check aria-hidden />{t("worker.complete")}</Button>
              {task.canSkip && <Button data-testid={`offline-skip-${task.id}`} variant="outline" className="h-11" disabled={busy.has(task.id)} onClick={() => void record(task.id, "skip")}><X aria-hidden />{t("worker.skip")}</Button>}
            </div> : <p className="mt-2 text-sm text-muted-foreground">{t("worker.offlineShift.formOnline")}</p>}
        </li>)}
      </ul>
      <Button variant="outline" className="h-11" onClick={() => void endShift()}>{t("worker.endShift")}</Button>
    </> : <p>{t("worker.offlineShift.unavailable")}</p>}
    <a href="/worker/login" className="inline-flex min-h-11 items-center rounded-lg border px-4 py-2 font-medium">{t("worker.offlineShift.signIn")}</a>
  </div>;
}

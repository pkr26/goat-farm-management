"use client";

/**
 * Disease-check walkthrough dialog: pick each farm bucket (pen), take a
 * photo of its goats, upload it straight to S3 via a presigned PUT, repeat.
 * "Finish & process" submits the batch — the worker screens every photo as
 * the bytes land and results show up in the review queue.
 *
 * The phone's bytes never touch the API: the backend mints the key
 * (raw/<farm>/<date>/<bucket>/…) and a short-lived PUT URL, and no AWS
 * credential ever reaches this device.
 */

import { Camera, CheckCircle2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  requestUploadApiScreeningUploadsPost,
  useBucketsBoardApiBucketsGet,
  useCreateBatchApiScreeningBatchesPost,
  useSubmitBatchApiScreeningBatchesBatchIdSubmitPost,
} from "@/api/generated/endpoints";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/api-client";
import { enumLabel } from "@/lib/enum-labels";
import { useLanguage, useT } from "@/lib/i18n";

const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

export function DiseaseCheckDialog({
  open,
  onOpenChange,
  onFinished,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a successful submit so the page can refresh its lists. */
  onFinished: () => void;
}) {
  const t = useT();
  const { language } = useLanguage();
  const createBatch = useCreateBatchApiScreeningBatchesPost();
  const submitBatch = useSubmitBatchApiScreeningBatchesBatchIdSubmitPost();
  const bucketsQuery = useBucketsBoardApiBucketsGet({
    query: { enabled: open },
  });
  const buckets =
    bucketsQuery.data?.status === 200 ? bucketsQuery.data.data : undefined;

  const [batchId, setBatchId] = useState<number | null>(null);
  const [selectedBucket, setSelectedBucket] = useState<string | null>(null);
  const [uploadedByBucket, setUploadedByBucket] = useState<Record<string, number>>({});
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Synchronous double-click lock: `disabled={uploading}` only applies after
  // the re-render, so two events delivered in the same React batch would both
  // enter uploadPendingPhoto and (with no batch yet) mint two ScreeningBatch
  // rows — the lazy-mint comment below exists precisely to avoid that (2026-09-17
  // audit L-20; the account-dialog beginAction pattern).
  const uploadInFlight = useRef(false);
  // Bumped on every open: an upload started under a previous walkthrough
  // session must not credit its photo to the freshly reset one.
  const walkthroughEpoch = useRef(0);

  // A fresh walkthrough starts clean on open. The batch itself is minted
  // lazily on the first successful upload attempt: opening the dialog to
  // look around (or losing signal before any photo) must not leave empty
  // ScreeningBatch rows piling up in the walkthrough list.
  useEffect(() => {
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setBatchId(null);
    setSelectedBucket(null);
    setUploadedByBucket({});
    setPendingFile(null);
    setPreviewUrl(null);
    // A stalled PUT from the closed session can never settle its `finally`
    // once it times out — but a session closed mid-upload must not leave the
    // reopened dialog inert with `uploading` stuck true (2026-09-17 audit M-11).
    setUploading(false);
    walkthroughEpoch.current += 1;
  }, [open]);

  // Revoke object URLs when the preview changes or the dialog closes.
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const onFileChosen = (file: File | null) => {
    if (!file) return;
    if (!["image/jpeg", "image/png"].includes(file.type)) {
      toast.error(t("screening.check.uploadFailed"));
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.error(t("screening.check.uploadFailed"));
      return;
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPendingFile(file);
    setPreviewUrl(URL.createObjectURL(file));
  };

  const ensureBatchId = async (): Promise<number | null> => {
    if (batchId !== null) return batchId;
    try {
      const result = await createBatch.mutateAsync(undefined);
      if (result.status === 201) {
        setBatchId(result.data.id);
        return result.data.id;
      }
      return null;
    } catch {
      toast.error(t("screening.check.noBatch"));
      return null;
    }
  };

  const uploadPendingPhoto = async () => {
    if (!pendingFile || !selectedBucket) return;
    if (uploadInFlight.current) return;
    uploadInFlight.current = true;
    const epoch = walkthroughEpoch.current;
    const stillCurrentSession = () => walkthroughEpoch.current === epoch;
    setUploading(true);
    try {
      const activeBatchId = await ensureBatchId();
      if (activeBatchId === null) return;
      const extension = pendingFile.type === "image/png" ? ".png" : ".jpg";
      const result = await requestUploadApiScreeningUploadsPost({
        batch_id: activeBatchId,
        bucket: selectedBucket as "BREEDING",
        file_name: `photo${extension}`,
        content_type: pendingFile.type as "image/jpeg" | "image/png",
      });
      if (result.status !== 201) return;
      // Direct PUT to S3 — the signed content type must be sent verbatim.
      // Bounded like every api-client request: on flaky mobile data an
      // unbounded PUT never settles, leaving the dialog's uploading state
      // wedged until a full page reload (2026-09-17 audit M-11).
      const response = await fetch(result.data.upload_url, {
        method: "PUT",
        body: pendingFile,
        headers: { "Content-Type": pendingFile.type },
        signal: AbortSignal.timeout(60_000),
      });
      if (!response.ok) {
        toast.error(t("screening.check.uploadFailed"));
        return;
      }
      // A session closed (and reopened) while this PUT was in flight owns a
      // fresh walkthrough: the photo belongs to the OLD session's batch, so
      // neither its counts nor its toasts may touch the new session's state
      // (2026-09-17 audit L-20).
      if (!stillCurrentSession()) return;
      setUploadedByBucket((counts) => ({
        ...counts,
        [selectedBucket]: (counts[selectedBucket] ?? 0) + 1,
      }));
      setPendingFile(null);
      setPreviewUrl(null);
      toast.success(t("screening.check.uploaded"));
    } catch (error) {
      if (!stillCurrentSession()) return;
      if (error instanceof ApiError && error.status === 409) {
        toast.error(t("screening.check.noBatch"));
      } else {
        toast.error(t("screening.check.uploadFailed"));
      }
    } finally {
      uploadInFlight.current = false;
      if (stillCurrentSession()) {
        setUploading(false);
      }
    }
  };

  const finishWalkthrough = async () => {
    if (batchId === null) return;
    const total = Object.values(uploadedByBucket).reduce((sum, count) => sum + count, 0);
    if (total === 0) {
      toast.error(t("screening.check.noPhotos"));
      return;
    }
    try {
      const result = await submitBatch.mutateAsync({ batchId });
      if (result.status !== 200) return;
      toast.success(t("screening.check.finished"));
      onOpenChange(false);
      onFinished();
    } catch {
      toast.error(t("screening.check.noBatch"));
    }
  };

  const totalUploaded = Object.values(uploadedByBucket).reduce(
    (sum, count) => sum + count,
    0,
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("screening.check.title")}</DialogTitle>
          <DialogDescription>{t("screening.check.description")}</DialogDescription>
        </DialogHeader>

        {selectedBucket === null ? (
          <div className="space-y-3">
            <p className="text-sm font-medium">{t("screening.check.buckets")}</p>
            <p className="text-sm text-muted-foreground">{t("screening.check.minHint")}</p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {(buckets ?? []).map((row) => (
                <button
                  key={row.bucket}
                  type="button"
                  className="rounded-lg border p-3 text-left text-sm transition-colors hover:bg-accent"
                  onClick={() => setSelectedBucket(row.bucket)}
                >
                  <span className="block font-medium">
                    {enumLabel("bucket", row.bucket, language)}
                  </span>
                  <span className="text-muted-foreground">
                    {t("screening.check.photosUploaded", {
                      count: uploadedByBucket[row.bucket] ?? 0,
                    })}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Badge variant="outline">
                {enumLabel("bucket", selectedBucket, language)}
              </Badge>
              <Button variant="ghost" size="sm" onClick={() => setSelectedBucket(null)}>
                {t("screening.check.buckets")}
              </Button>
            </div>

            {previewUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={previewUrl}
                alt={t("screening.check.takePhoto")}
                className="max-h-[40vh] w-full rounded-lg border object-contain"
              />
            ) : (
              <div className="flex h-40 items-center justify-center rounded-lg border border-dashed text-muted-foreground">
                <Camera className="mr-2" aria-hidden />
                {t("screening.check.takePhoto")}
              </div>
            )}

            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png"
              capture="environment"
              className="hidden"
              onChange={(event) => {
                onFileChosen(event.target.files?.[0] ?? null);
                event.target.value = "";
              }}
            />

            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
              >
                <Camera aria-hidden />
                {pendingFile ? t("screening.check.retake") : t("screening.check.takePhoto")}
              </Button>
              <Button
                onClick={uploadPendingPhoto}
                disabled={pendingFile === null || uploading}
              >
                {uploading ? (
                  t("screening.check.uploading")
                ) : (
                  <>
                    <Upload aria-hidden /> {t("screening.check.uploadPhoto")}
                  </>
                )}
              </Button>
            </div>
          </div>
        )}

        <DialogFooter className="items-center gap-2 sm:justify-between">
          <div className="flex items-center gap-1 text-sm text-muted-foreground">
            <CheckCircle2 aria-hidden />
            {t("screening.check.photosUploaded", { count: totalUploaded })}
          </div>
          <Button
            onClick={finishWalkthrough}
            disabled={submitBatch.isPending || totalUploaded === 0}
          >
            {submitBatch.isPending
              ? t("screening.check.finishing")
              : t("screening.check.finish")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

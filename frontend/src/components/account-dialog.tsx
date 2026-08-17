"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Download, KeyRound, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { useChangePasswordApiAuthChangePasswordPost } from "@/api/generated/endpoints";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiFetch, refreshSession } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { farmToday } from "@/lib/format";

const passwordSchema = z
  .object({
    current_password: z.string().min(1, "Current password is required").max(128),
    new_password: z.string().min(12, "Password must be at least 12 characters").max(128),
    confirm_password: z.string().min(1, "Confirm the new password").max(128),
  })
  .refine((values) => values.new_password === values.confirm_password, {
    path: ["confirm_password"],
    message: "Passwords do not match",
  });

type PasswordValues = z.infer<typeof passwordSchema>;

export function AccountDialog({ name, email }: { name: string | null; email: string }) {
  const { signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [deleteMode, setDeleteMode] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const dialogEpoch = useRef(0);
  const mutation = useChangePasswordApiAuthChangePasswordPost();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<PasswordValues>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { current_password: "", new_password: "", confirm_password: "" },
  });

  function close() {
    // Async actions may settle after the user closes the dialog. Advance the
    // epoch so a late failure cannot repopulate an error that close() just
    // cleared and then surprise the user on the next open.
    dialogEpoch.current += 1;
    setOpen(false);
    setServerError(null);
    setExportError(null);
    setDeleteMode(false);
    setDeletePassword("");
    setDeleteError(null);
    reset();
  }

  async function downloadExport() {
    const operationEpoch = dialogEpoch.current;
    setExporting(true);
    setExportError(null);
    try {
      const payload = await apiFetch<unknown>("/api/auth/account/export");
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = `goatfarm-account-export-${farmToday()}.json`;
      try {
        document.body.appendChild(anchor);
        anchor.click();
      } finally {
        anchor.remove();
        URL.revokeObjectURL(href);
      }
      toast.success("Your account data export was downloaded.");
    } catch (error) {
      if (operationEpoch === dialogEpoch.current) {
        setExportError(
          error instanceof ApiError ? error.detail : "Could not download your account data.",
        );
      }
    } finally {
      setExporting(false);
    }
  }

  async function deleteAccount() {
    if (!deletePassword) return;
    const operationEpoch = dialogEpoch.current;
    setDeleting(true);
    setDeleteError(null);
    try {
      await apiFetch<void>("/api/auth/account", {
        method: "DELETE",
        body: JSON.stringify({ current_password: deletePassword }),
      });
      toast.success(
        "Your sign-in identity and profile were removed, and your farm access was disabled. Inactive membership audit anchors and de-identified operational references may remain.",
      );
      await signOut();
    } catch (error) {
      if (operationEpoch === dialogEpoch.current) {
        setDeleteError(
          error instanceof ApiError ? error.detail : "Could not delete your account.",
        );
      }
    } finally {
      setDeleting(false);
    }
  }

  async function onSubmit(values: PasswordValues) {
    const operationEpoch = dialogEpoch.current;
    setServerError(null);
    try {
      const response = await mutation.mutateAsync({
        data: {
          current_password: values.current_password,
          new_password: values.new_password,
        },
      });
      if (response.status === 200) {
        // The server revoked every other session and rotated THIS device's
        // refresh cookie; the old in-memory access token is now invalid. But
        // installing the returned token via setAccessToken would bump
        // authSessionEpoch — the "different session signed in" boundary — and
        // abort every concurrent in-flight request with
        // AuthSessionChangedError. The actor is unchanged here, so pull a
        // fresh token through refreshSession(), the same internal path a 401
        // retry uses: it swaps the token in place without invalidating
        // requests already on the wire (and any request that races the swap
        // self-heals through the ordinary 401 → refresh retry).
        const refreshed = await refreshSession().catch(() => null);
        if (!refreshed) {
          toast.success("Password changed. Sign in again to continue.");
          close();
          await signOut();
          return;
        }
      }
      toast.success("Password changed. Other signed-in sessions were revoked.");
      close();
    } catch (error) {
      if (operationEpoch === dialogEpoch.current) {
        setServerError(error instanceof ApiError ? error.detail : "Could not change the password.");
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
      <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>
        <KeyRound aria-hidden />
        Account
      </Button>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Account & password</DialogTitle>
        </DialogHeader>
        <div className="rounded-lg bg-muted/60 p-3 text-sm">
          <p className="font-medium">{name ?? email}</p>
          {name && <p className="text-muted-foreground">{email}</p>}
        </div>
        <section className="space-y-2" aria-labelledby="account-data-heading">
          <h3 id="account-data-heading" className="text-sm font-medium">
            Your data
          </h3>
          <p className="text-xs text-muted-foreground">
            Download a JSON copy of your account, owned farms and memberships.
          </p>
          {exportError && (
            <p role="alert" className="text-sm text-destructive">
              {exportError}
            </p>
          )}
          <Button
            type="button"
            variant="outline"
            onClick={() => void downloadExport()}
            disabled={exporting}
          >
            <Download aria-hidden />
            {exporting ? "Preparing…" : "Download my data"}
          </Button>
        </section>
        <form
          onSubmit={(event) => {
            // Construct react-hook-form's submit handler at event time. The
            // callback reads the dialog lifecycle ref, which must never be
            // evaluated during render.
            void handleSubmit(onSubmit)(event);
          }}
          className="space-y-4"
          aria-labelledby="change-password-heading"
          noValidate
        >
          <h3 id="change-password-heading" className="text-sm font-medium">
            Change password
          </h3>
          {serverError && (
            <p role="alert" className="text-sm text-destructive">
              {serverError}
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="account-current-password">Current password for password change</Label>
            <Input
              id="account-current-password"
              type="password"
              autoComplete="current-password"
              aria-invalid={Boolean(errors.current_password) || undefined}
              aria-describedby={errors.current_password ? "account-current-password-error" : undefined}
              {...register("current_password")}
            />
            {errors.current_password && (
              <p id="account-current-password-error" role="alert" className="text-sm text-destructive">
                {errors.current_password.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="account-new-password">New password</Label>
            <Input
              id="account-new-password"
              type="password"
              autoComplete="new-password"
              aria-invalid={Boolean(errors.new_password) || undefined}
              aria-describedby={errors.new_password ? "account-new-password-error" : undefined}
              {...register("new_password")}
            />
            {errors.new_password && (
              <p id="account-new-password-error" role="alert" className="text-sm text-destructive">
                {errors.new_password.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="account-confirm-password">Confirm new password</Label>
            <Input
              id="account-confirm-password"
              type="password"
              autoComplete="new-password"
              aria-invalid={Boolean(errors.confirm_password) || undefined}
              aria-describedby={errors.confirm_password ? "account-confirm-password-error" : undefined}
              {...register("confirm_password")}
            />
            {errors.confirm_password && (
              <p id="account-confirm-password-error" role="alert" className="text-sm text-destructive">
                {errors.confirm_password.message}
              </p>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            Changing your password signs out every other device and keeps this one active.
          </p>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Changing…" : "Change password"}
            </Button>
          </DialogFooter>
        </form>
        <section
          className="space-y-3 rounded-lg border border-destructive/40 p-3"
          aria-labelledby="delete-account-heading"
        >
          <h3 id="delete-account-heading" className="text-sm font-medium text-destructive">
            Delete account
          </h3>
          <p className="text-xs text-muted-foreground">
            Deletion removes your sign-in identity and profile and disables your farm access.
            Inactive membership rows and operational records may retain a pseudonymous audit
            reference.
            If you own any farm, deletion is blocked so its records cannot be orphaned.
          </p>
          {!deleteMode ? (
            <Button
              type="button"
              variant="destructive"
              onClick={() => setDeleteMode(true)}
            >
              <Trash2 aria-hidden />
              Delete my account…
            </Button>
          ) : (
            <div className="space-y-3">
              <p className="text-sm font-medium">
                Enter your current password to confirm deletion of your sign-in identity and farm
                access.
              </p>
              {deleteError && (
                <p role="alert" className="text-sm text-destructive">
                  {deleteError}
                </p>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="account-delete-password">Current password to delete account</Label>
                <Input
                  id="account-delete-password"
                  type="password"
                  autoComplete="current-password"
                  value={deletePassword}
                  onChange={(event) => setDeletePassword(event.target.value)}
                />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="destructive"
                  disabled={!deletePassword || deleting}
                  onClick={() => void deleteAccount()}
                >
                  {deleting ? "Deleting…" : "Delete account and access"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={deleting}
                  onClick={() => {
                    setDeleteMode(false);
                    setDeletePassword("");
                    setDeleteError(null);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </section>
      </DialogContent>
    </Dialog>
  );
}

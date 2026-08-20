"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Download, KeyRound, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { useChangePasswordApiAuthChangePasswordPost } from "@/api/generated/endpoints";
import type { AccountDeleteIn, AccountExportOut } from "@/api/generated/models";
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
import { ApiError, apiFetch, refreshSessionDetailed } from "@/lib/api-client";
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
type AccountAction = "export" | "password" | "delete";

export function AccountDialog({ name, email }: { name: string | null; email: string }) {
  const { signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [deleteMode, setDeleteMode] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [activeAction, setActiveAction] = useState<AccountAction | null>(null);
  const activeActionRef = useRef<AccountAction | null>(null);
  const dialogEpoch = useRef(0);
  const mounted = useRef(true);
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

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      // The dialog can disappear with the surrounding shell without close()
      // running. Invalidate late failures so they never target a dead
      // component lifecycle.
      dialogEpoch.current += 1;
    };
  }, []);

  function beginAction(action: AccountAction) {
    // Disabled buttons update on the next render. The ref is the synchronous
    // lock that also covers two events delivered in the same React batch and
    // prevents different account operations from racing each other.
    if (activeActionRef.current !== null) return false;
    activeActionRef.current = action;
    setActiveAction(action);
    return true;
  }

  function finishAction(action: AccountAction) {
    if (activeActionRef.current !== action) return;
    activeActionRef.current = null;
    if (mounted.current) setActiveAction(null);
  }

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
    if (!beginAction("export")) return;
    const operationEpoch = dialogEpoch.current;
    setExportError(null);
    try {
      const payload = await apiFetch<AccountExportOut>("/api/auth/account/export");
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
      finishAction("export");
    }
  }

  async function deleteAccount() {
    if (!deletePassword || !beginAction("delete")) return;
    const operationEpoch = dialogEpoch.current;
    setDeleteError(null);
    try {
      const payload = {
        current_password: deletePassword,
      } satisfies AccountDeleteIn;
      await apiFetch<void>("/api/auth/account", {
        method: "DELETE",
        body: JSON.stringify(payload),
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
      finishAction("delete");
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
        const outcome = await refreshSessionDetailed().catch(
          () => ({ kind: "unavailable" }) as const,
        );
        if (outcome.kind === "rejected") {
          // The server answered: the rotated cookie will not produce a token.
          toast.success("Password changed. Sign in again to continue.");
          if (operationEpoch === dialogEpoch.current && mounted.current) close();
          await signOut();
          return;
        }
        if (outcome.kind === "unavailable") {
          // We never reached the server, so this is NOT evidence the session
          // ended. Signing out here would revoke a refresh cookie the backend
          // still honours, over one transient 5xx or dropped request — and
          // that is unrecoverable. Keep the installed token; the ordinary
          // 401 → refresh retry self-heals once the network is back.
          toast.success("Password changed. Reconnecting to your session…");
          if (operationEpoch === dialogEpoch.current && mounted.current) close();
          return;
        }
      }
      toast.success("Password changed. Other signed-in sessions were revoked.");
      if (operationEpoch === dialogEpoch.current && mounted.current) close();
    } catch (error) {
      if (operationEpoch === dialogEpoch.current) {
        setServerError(error instanceof ApiError ? error.detail : "Could not change the password.");
      }
    }
  }

  function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!beginAction("password")) return;
    const submission = handleSubmit(onSubmit)(event);
    const release = () => finishAction("password");
    // A detached finally() creates a second rejected promise if validation or
    // an unexpected handler path throws. Two-branch settlement releases the
    // lock without publishing an unhandled rejection.
    void submission.then(release, release);
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
            disabled={activeAction !== null}
          >
            <Download aria-hidden />
            {activeAction === "export" ? "Preparing…" : "Download my data"}
          </Button>
        </section>
        <form
          onSubmit={submitPassword}
          className="space-y-4"
          aria-labelledby="change-password-heading"
          noValidate
        >
          <fieldset disabled={activeAction !== null || isSubmitting} className="space-y-4">
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
            <Button type="submit" disabled={activeAction !== null || isSubmitting}>
              {activeAction === "password" || isSubmitting ? "Changing…" : "Change password"}
            </Button>
          </DialogFooter>
          </fieldset>
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
              disabled={activeAction !== null}
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
                  disabled={activeAction !== null}
                  autoComplete="current-password"
                  value={deletePassword}
                  onChange={(event) => setDeletePassword(event.target.value)}
                />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="destructive"
                  disabled={!deletePassword || activeAction !== null}
                  onClick={() => void deleteAccount()}
                >
                  {activeAction === "delete" ? "Deleting…" : "Delete account and access"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={activeAction === "delete"}
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

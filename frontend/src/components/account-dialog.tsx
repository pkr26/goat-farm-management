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
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  apiFetch,
  authSessionEpochValue,
  refreshSessionDetailed,
} from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { farmToday } from "@/lib/format";
import { useT } from "@/lib/i18n";

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
  const { signOut, updateUser, user } = useAuth();
  const t = useT();
  const [open, setOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [deleteMode, setDeleteMode] = useState(false);
  // Two-factor (TOTP) section state. enrollment holds the secret returned by
  // the enroll endpoint until a code confirms it.
  const [totpMode, setTotpMode] = useState<"idle" | "enable" | "disable" | "regen">("idle");
  const [totpEnrollment, setTotpEnrollment] = useState<{
    secret: string;
    otpauth_uri: string;
  } | null>(null);
  const [totpPassword, setTotpPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [totpBusy, setTotpBusy] = useState(false);
  const [totpError, setTotpError] = useState<string | null>(null);
  // ITEM 7 (2026-09-21 playbook): the one-time recovery-code reveal. Set the
  // moment activation/regeneration answers, cleared with the dialog — the
  // server can never show these again.
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [codesCopied, setCodesCopied] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [activeAction, setActiveAction] = useState<AccountAction | null>(null);
  const activeActionRef = useRef<AccountAction | null>(null);
  const dialogEpoch = useRef(0);
  // Stryker disable next-line BooleanLiteral: the mount effect below overwrites the initial value before any continuation can observe it
  const mounted = useRef(true);
  const mutation = useChangePasswordApiAuthChangePasswordPost();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<PasswordValues>({
    resolver: zodResolver(passwordSchema),
    // Stryker disable next-line ObjectLiteral: undefined defaults are observably identical to empty strings for these always-empty fields
    defaultValues: { current_password: "", new_password: "", confirm_password: "" },
  });

  // Stryker disable BlockStatement, ArrayDeclaration: an empty effect body leaves the already-true ref value in place, and a constant string dep still runs the effect exactly once
  useEffect(() => {
    mounted.current = true;
    // Stryker disable next-line BlockStatement: unmount-only — the refs die with the instance
    return () => {
      // Stryker disable BooleanLiteral, AssignmentOperator: unmount-only writes — the refs die with the instance
      mounted.current = false;
      // The dialog can disappear with the surrounding shell without close()
      // running. Invalidate late failures so they never target a dead
      // component lifecycle.
      dialogEpoch.current += 1;
    };
  }, []);
  // Stryker restore BlockStatement, ArrayDeclaration, BooleanLiteral, AssignmentOperator

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
    // Stryker disable next-line ConditionalExpression: a mismatched finish is unreachable — beginAction's ref lock serializes account actions
    if (activeActionRef.current !== action) return;
    activeActionRef.current = null;
    // setState after unmount is a React no-op; no mounted re-check needed.
    setActiveAction(null);
  }

  function close() {
    // Async actions may settle after the user closes the dialog. Advance the
    // epoch so a late failure cannot repopulate an error that close() just
    // cleared and then surprise the user on the next open.
    // Stryker disable next-line AssignmentOperator: every consumer compares epochs for equality only, and a monotonic decrease yields fresh distinct values exactly like the increment
    dialogEpoch.current += 1;
    setOpen(false);
    setServerError(null);
    setExportError(null);
    setDeleteMode(false);
    setDeletePassword("");
    setDeleteError(null);
    setTotpMode("idle");
    setTotpEnrollment(null);
    setTotpPassword("");
    setTotpCode("");
    setTotpError(null);
    setRecoveryCodes(null);
    setCodesCopied(false);
    reset();
  }

  async function enrollTotp() {
    // Async actions may settle after the user closes the dialog (close()
    // bumps the epoch and clears the enrollment); without the fence a late
    // response resurrected a stale secret the server had already replaced
    // (2026-09-17 re-audit).
    const operationEpoch = dialogEpoch.current;
    setTotpBusy(true);
    setTotpError(null);
    try {
      const enrollment = await apiFetch<{ secret: string; otpauth_uri: string }>(
        "/api/auth/totp/enroll",
        { method: "POST", body: JSON.stringify({ current_password: totpPassword }) },
      );
      if (operationEpoch !== dialogEpoch.current) return;
      setTotpEnrollment(enrollment);
      setTotpCode("");
    } catch (err) {
      if (operationEpoch === dialogEpoch.current) {
        setTotpError(err instanceof ApiError ? err.detail : t("totp.networkError"));
      }
    } finally {
      if (operationEpoch === dialogEpoch.current) setTotpBusy(false);
    }
  }

  async function confirmTotp() {
    if (totpEnrollment === null) return;
    const operationEpoch = dialogEpoch.current;
    setTotpBusy(true);
    setTotpError(null);
    try {
      const reveal = await apiFetch<{ codes: string[] }>("/api/auth/totp/confirm", {
        method: "POST",
        body: JSON.stringify({ code: totpCode }),
      });
      if (operationEpoch !== dialogEpoch.current) return;
      setTotpEnrollment(null);
      setTotpMode("idle");
      setTotpCode("");
      setTotpPassword("");
      setRecoveryCodes(reveal.codes);
      setCodesCopied(false);
      if (user) updateUser({ ...user, totp_state: "ACTIVE" });
      toast.success(t("totp.enabledToast"));
    } catch (err) {
      if (operationEpoch === dialogEpoch.current) {
        setTotpError(err instanceof ApiError ? err.detail : t("totp.networkError"));
      }
    } finally {
      if (operationEpoch === dialogEpoch.current) setTotpBusy(false);
    }
  }

  async function disableTotp() {
    const operationEpoch = dialogEpoch.current;
    setTotpBusy(true);
    setTotpError(null);
    try {
      await apiFetch<void>("/api/auth/totp/disable", {
        method: "POST",
        body: JSON.stringify({ current_password: totpPassword, code: totpCode }),
      });
      if (operationEpoch !== dialogEpoch.current) return;
      setTotpMode("idle");
      setTotpCode("");
      setTotpPassword("");
      if (user) updateUser({ ...user, totp_state: null });
      toast.success(t("totp.disabledToast"));
    } catch (err) {
      if (operationEpoch === dialogEpoch.current) {
        setTotpError(err instanceof ApiError ? err.detail : t("totp.networkError"));
      }
    } finally {
      if (operationEpoch === dialogEpoch.current) setTotpBusy(false);
    }
  }

  async function regenerateRecoveryCodes() {
    const operationEpoch = dialogEpoch.current;
    setTotpBusy(true);
    setTotpError(null);
    try {
      const reveal = await apiFetch<{ codes: string[] }>(
        "/api/auth/totp/recovery/regenerate",
        {
          method: "POST",
          body: JSON.stringify({ current_password: totpPassword, code: totpCode }),
        },
      );
      if (operationEpoch !== dialogEpoch.current) return;
      setTotpMode("idle");
      setTotpCode("");
      setTotpPassword("");
      setRecoveryCodes(reveal.codes);
      setCodesCopied(false);
    } catch (err) {
      if (operationEpoch === dialogEpoch.current) {
        setTotpError(err instanceof ApiError ? err.detail : t("totp.networkError"));
      }
    } finally {
      if (operationEpoch === dialogEpoch.current) setTotpBusy(false);
    }
  }

  function copyRecoveryCodes() {
    if (recoveryCodes === null) return;
    void navigator.clipboard?.writeText(recoveryCodes.join("\n")).then(() => {
      setCodesCopied(true);
      toast.success(t("totp.recoveryCopied"));
    });
  }

  async function downloadExport() {
    if (!beginAction("export")) return;
    const operationEpoch = dialogEpoch.current;
    const sessionEpoch = authSessionEpochValue();
    setExportError(null);
    try {
      const payload = await apiFetch<AccountExportOut>("/api/auth/account/export");
      if (authSessionEpochValue() !== sessionEpoch) return;
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = `herdly-account-export-${farmToday()}.json`;
      try {
        document.body.appendChild(anchor);
        anchor.click();
      } finally {
        anchor.remove();
        URL.revokeObjectURL(href);
      }
      // The download itself was explicitly user-initiated and already
      // happened; only the completion toast is fenced, so closing the dialog
      // mid-download (which bumps dialogEpoch) does not surface a stale
      // toast for a lifecycle the operator already abandoned — the same
      // double fence the error path below uses.
      if (operationEpoch === dialogEpoch.current) {
        toast.success("Your account data export was downloaded.");
      }
    } catch (error) {
      if (
        authSessionEpochValue() === sessionEpoch &&
        operationEpoch === dialogEpoch.current
      ) {
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
    const sessionEpoch = authSessionEpochValue();
    setDeleteError(null);
    try {
      const payload = {
        current_password: deletePassword,
      } satisfies AccountDeleteIn;
      await apiFetch<void>("/api/auth/account", {
        method: "DELETE",
        body: JSON.stringify(payload),
      });
      if (authSessionEpochValue() !== sessionEpoch) return;
      toast.success(
        "Your sign-in identity and profile were removed, and your farm access was disabled. Inactive membership audit anchors and de-identified operational references may remain.",
      );
      await signOut();
    } catch (error) {
      if (
        authSessionEpochValue() === sessionEpoch &&
        operationEpoch === dialogEpoch.current
      ) {
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
    // The password mutation and its follow-up refresh are one logical action
    // for the session that submitted the form. The API client fences the
    // mutation itself, but a replacement login can still land in the gap
    // between that promise resolving and the refresh below settling. Never
    // let this old dialog sign out or report against that newer session.
    const sessionEpoch = authSessionEpochValue();
    setServerError(null);
    try {
      const response = await mutation.mutateAsync({
        data: {
          current_password: values.current_password,
          new_password: values.new_password,
        },
      });
      if (authSessionEpochValue() !== sessionEpoch) return;
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
        if (authSessionEpochValue() !== sessionEpoch) return;
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
        // The refresh answer carried the post-rotation user; installing it
        // clears the must-change-password banner the moment the requirement
        // is actually satisfied, instead of on the next full reload.
        updateUser(outcome.body.user);
      }
      toast.success("Password changed. Other signed-in sessions were revoked.");
      if (operationEpoch === dialogEpoch.current && mounted.current) close();
    } catch (error) {
      if (
        authSessionEpochValue() === sessionEpoch &&
        operationEpoch === dialogEpoch.current
      ) {
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
      {/* A registered trigger (not a detached setOpen button) so base-ui can
       * restore focus to this exact button when the dialog closes. */}
      <DialogTrigger
        render={
          <Button
            variant="ghost"
            size="sm"
            className="gap-2 px-1.5 font-normal"
            aria-label={`Account — ${name ?? email}`}
          />
        }
      >
        <span
          aria-hidden="true"
          className="flex size-6 items-center justify-center rounded-full bg-primary/10 text-[0.65rem] font-semibold text-primary"
        >
          {(name ?? email).trim().slice(0, 2).toUpperCase()}
        </span>
        <span className="hidden max-w-32 truncate md:inline">{name ?? email}</span>
        <KeyRound aria-hidden className="size-3.5 text-muted-foreground" />
      </DialogTrigger>
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
              type="password" maxLength={128}
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
              type="password" maxLength={128}
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
              type="password" maxLength={128}
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
              {/* react-hook-form's isSubmitting spans the entire password-action
                 window (beginAction runs inside the submit handler), so it
                 alone decides the label. */}
              {isSubmitting ? "Changing…" : "Change password"}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
        <section className="space-y-3" aria-labelledby="totp-heading">
          <h3 id="totp-heading" className="text-sm font-medium">
            {t("totp.title")}
          </h3>
          {recoveryCodes !== null ? (
            <div className="space-y-2 rounded-lg border p-3" aria-live="polite">
              <h4 className="text-sm font-medium">{t("totp.recoveryTitle")}</h4>
              <p className="text-xs text-destructive">{t("totp.recoveryShownOnce")}</p>
              <ol className="grid grid-cols-2 gap-1 font-mono text-sm sm:grid-cols-2">
                {recoveryCodes.map((code) => (
                  <li key={code} className="select-all">
                    {code}
                  </li>
                ))}
              </ol>
              <Button type="button" variant="outline" size="sm" onClick={copyRecoveryCodes}>
                {codesCopied ? t("totp.recoveryCopied") : t("totp.recoveryCopy")}
              </Button>
            </div>
          ) : null}
          {user?.totp_state === "ACTIVE" ? (
            <>
              <p className="text-xs text-muted-foreground">{t("totp.activeDescr")}</p>
              {totpMode === "disable" ? (
                <div className="space-y-2">
                  <Label htmlFor="totp-disable-password">{t("totp.currentPassword")}</Label>
                  <Input
                    id="totp-disable-password"
                    type="password"
                    autoComplete="current-password"
                    maxLength={128}
                    value={totpPassword}
                    onChange={(e) => setTotpPassword(e.target.value)}
                  />
                  <Label htmlFor="totp-disable-code">{t("login.totpCodeLabel")}</Label>
                  <Input
                    id="totp-disable-code"
                    inputMode="numeric"
                    maxLength={6}
                    value={totpCode}
                    onChange={(e) => setTotpCode(e.target.value)}
                  />
                  {totpError && (
                    <p role="alert" className="text-sm text-destructive">
                      {totpError}
                    </p>
                  )}
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="destructive"
                      disabled={totpBusy || activeAction !== null}
                      onClick={() => void disableTotp()}
                    >
                      {totpBusy ? t("totp.disabling") : t("totp.disableConfirm")}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={totpBusy}
                      onClick={() => {
                        setTotpMode("idle");
                        setTotpError(null);
                      }}
                    >
                      {t("common.cancel")}
                    </Button>
                  </div>
                </div>
              ) : totpMode === "regen" ? (
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">{t("totp.regenIntro")}</p>
                  <Label htmlFor="totp-regen-password">{t("totp.currentPassword")}</Label>
                  <Input
                    id="totp-regen-password"
                    type="password"
                    autoComplete="current-password"
                    maxLength={128}
                    value={totpPassword}
                    onChange={(e) => setTotpPassword(e.target.value)}
                  />
                  <Label htmlFor="totp-regen-code">{t("login.totpCodeLabel")}</Label>
                  <Input
                    id="totp-regen-code"
                    inputMode="numeric"
                    maxLength={6}
                    value={totpCode}
                    onChange={(e) => setTotpCode(e.target.value)}
                  />
                  {totpError && (
                    <p role="alert" className="text-sm text-destructive">
                      {totpError}
                    </p>
                  )}
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      disabled={totpBusy || activeAction !== null}
                      onClick={() => void regenerateRecoveryCodes()}
                    >
                      {totpBusy ? t("totp.regenBusy") : t("totp.regenConfirm")}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={totpBusy}
                      onClick={() => {
                        setTotpMode("idle");
                        setTotpError(null);
                      }}
                    >
                      {t("common.cancel")}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={activeAction !== null}
                    onClick={() => {
                      setTotpMode("disable");
                      setTotpPassword("");
                      setTotpCode("");
                      setTotpError(null);
                    }}
                  >
                    {t("totp.disable")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={activeAction !== null}
                    onClick={() => {
                      setTotpMode("regen");
                      setTotpPassword("");
                      setTotpCode("");
                      setTotpError(null);
                    }}
                  >
                    {t("totp.recoveryRegenerate")}
                  </Button>
                </div>
              )}
            </>
          ) : totpEnrollment ? (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">{t("totp.enrollLinkHint")}</p>
              {/* The only API-derived href in the app that never passed
               * safeAppPath: the backend contract is otpauth://, so anything
               * else is a tampered/compromised response and must render as
               * inert text, never as a clickable javascript: link
               * (2026-09-17 audit L-19). */}
              {totpEnrollment.otpauth_uri.startsWith("otpauth://") ? (
                <a
                  className="block break-all rounded bg-muted/60 p-2 font-mono text-xs underline"
                  href={totpEnrollment.otpauth_uri}
                >
                  {totpEnrollment.otpauth_uri}
                </a>
              ) : (
                <p className="block break-all rounded bg-muted/60 p-2 font-mono text-xs">
                  {totpEnrollment.otpauth_uri}
                </p>
              )}
              <p className="break-all rounded bg-muted/60 p-2 font-mono text-sm">
                {totpEnrollment.secret}
              </p>
              <Label htmlFor="totp-confirm-code">{t("login.totpCodeLabel")}</Label>
              <Input
                id="totp-confirm-code"
                inputMode="numeric"
                maxLength={6}
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
              />
              {totpError && (
                <p role="alert" className="text-sm text-destructive">
                  {totpError}
                </p>
              )}
              <div className="flex gap-2">
                <Button
                  type="button"
                  disabled={totpBusy || activeAction !== null}
                  onClick={() => void confirmTotp()}
                >
                  {totpBusy ? t("totp.activating") : t("totp.activate")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={totpBusy}
                  onClick={() => {
                    setTotpEnrollment(null);
                    setTotpError(null);
                  }}
                >
                  {t("common.cancel")}
                </Button>
              </div>
            </div>
          ) : totpMode === "enable" ? (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">{t("totp.enableIntro")}</p>
              <Label htmlFor="totp-enable-password">{t("totp.currentPassword")}</Label>
              <Input
                id="totp-enable-password"
                type="password"
                autoComplete="current-password"
                maxLength={128}
                value={totpPassword}
                onChange={(e) => setTotpPassword(e.target.value)}
              />
              {totpError && (
                <p role="alert" className="text-sm text-destructive">
                  {totpError}
                </p>
              )}
              <div className="flex gap-2">
                <Button
                  type="button"
                  disabled={totpBusy || activeAction !== null}
                  onClick={() => void enrollTotp()}
                >
                  {totpBusy ? t("totp.starting") : t("totp.startEnrollment")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={totpBusy}
                  onClick={() => {
                    setTotpMode("idle");
                    setTotpError(null);
                  }}
                >
                  {t("common.cancel")}
                </Button>
              </div>
            </div>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">{t("totp.offDescr")}</p>
              <Button
                type="button"
                variant="outline"
                disabled={activeAction !== null}
                onClick={() => {
                  setTotpMode("enable");
                  setTotpError(null);
                }}
              >
                {t("totp.enable")}
              </Button>
            </>
          )}
        </section>
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
                  type="password" maxLength={128}
                  disabled={activeAction !== null}
                  autoComplete="current-password"
                  value={deletePassword}
                  onChange={(event) => setDeletePassword(event.target.value)}
                />
              </div>
              <div className="flex flex-wrap gap-2">
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
                <Button
                  type="button"
                  variant="destructive"
                  disabled={!deletePassword || activeAction !== null}
                  onClick={() => void deleteAccount()}
                >
                  {activeAction === "delete" ? "Deleting…" : "Delete account and access"}
                </Button>
              </div>
            </div>
          )}
        </section>
      </DialogContent>
    </Dialog>
  );
}

"use client";

/** Team management: workers, roles, permission matrix — parity with v1's team/list.html + worker_new.html + role_form.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Lock, ShieldCheck, Users } from "lucide-react";
import { useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getPermissionsApiAuthPermissionsGetQueryKey,
  getTeamPageApiTeamGetQueryKey,
  useChangeRoleApiTeamWorkersMembershipIdRolePost,
  useCreateRoleApiTeamRolesPost,
  useCreateWorkerApiTeamWorkersPost,
  useDeleteRoleApiTeamRolesRoleIdDelete,
  useResetPasswordApiTeamWorkersMembershipIdResetPasswordPost,
  useSetWorkerStatusApiTeamWorkersMembershipIdStatusPut,
  useTeamPageApiTeamGet,
  useUpdateRoleApiTeamRolesRoleIdPut,
} from "@/api/generated/endpoints";
import type { MembershipOut, RoleOut, TeamOut } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { PageSkeleton } from "@/components/skeletons";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api-client";
import { mutationError } from "@/lib/mutations";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { useAuth } from "@/lib/auth-context";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

/** Sentinel for "no role" (empty string is not a valid item value). */
const NONE = "none";

type TeamAuthority = {
  blocked: boolean;
  canStart: () => boolean;
};


function useInvalidateTeam() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: getTeamPageApiTeamGetQueryKey() });
    // A role edit can change the EDITOR's own grants (including their own
    // membership row); /api/auth/permissions is deliberately excluded from
    // invalidateFarmData, so refresh it explicitly or elevated buttons stay
    // live on stale grants (M-5).
    queryClient.invalidateQueries({
      queryKey: getPermissionsApiAuthPermissionsGetQueryKey(),
    });
  };
}

const workerSchema = z.object({
  name: z.string().trim().max(120).optional(),
  email: z
    .string()
    .trim()
    .email("Enter a valid email address")
    .max(254, "Email must be at most 254 characters"),
  role_id: z.string().min(1, "Pick a role"),
  password: z
    .string()
    .min(12, "Password must be at least 12 characters")
    .max(128, "Password must be at most 128 characters"),
});
type WorkerValues = z.infer<typeof workerSchema>;

const resetSchema = z.object({
  password: z.string().min(12, "Password must be at least 12 characters").max(128),
});
type ResetValues = z.infer<typeof resetSchema>;

const roleSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(80),
  description: z.string().trim().max(255).optional(),
});
type RoleValues = z.infer<typeof roleSchema>;

/** Every action is unusable in the UI unless its module's view permission is
 * also present. Keep this dependency visible and enforce it while roles are
 * edited instead of allowing action-only combinations that lead to a page
 * denial or a missing button. */
const PERMISSION_DEPENDENCIES: Record<string, string> = {
  "animals.create": "animals.view",
  "animals.move": "animals.view",
  "animals.weight": "animals.view",
  "animals.status": "animals.view",
  "breeding.manage": "breeding.view",
  "kidding.manage": "kidding.view",
  "health.manage": "health.view",
  "purchases.manage": "purchases.view",
  "feeding.manage": "feeding.view",
  "milk.manage": "milk.view",
  "milk.quality": "milk.manage",
  "tasks.create": "tasks.view",
  "tasks.complete": "tasks.view",
  "tasks.verify": "tasks.view",
  "finance.manage": "finance.view",
  "simulation.manage": "simulation.view",
};

function roleWithinCeiling(
  role: RoleOut,
  can: (code: string) => boolean,
  isOwner: boolean,
): boolean {
  return (
    isOwner ||
    (!role.permissions.includes("team.manage") && role.permissions.every((code) => can(code)))
  );
}

/** Per-row worker controls: role reassign, activate/deactivate (never for self), reset password. */
function WorkerRow({
  m,
  roles,
  can,
  isSelf,
  protectedTarget,
  isOwner,
  onReset,
  authority,
}: {
  m: MembershipOut;
  roles: RoleOut[];
  can: (code: string) => boolean;
  isSelf: boolean;
  protectedTarget: boolean;
  isOwner: boolean;
  onReset: (m: MembershipOut, release: () => void) => void;
  authority: TeamAuthority;
}) {
  const invalidate = useInvalidateTeam();
  const roleMutation = useChangeRoleApiTeamWorkersMembershipIdRolePost();
  const statusMutation = useSetWorkerStatusApiTeamWorkersMembershipIdStatusPut();
  // One lock covers BOTH row actions, but each control used to disable only on
  // its OWN mutation. The role Select therefore stayed interactive during a
  // status change, and the lock refused the pick as its very first statement —
  // no request, no error, and (being a fully controlled Select) no visible
  // movement at all. Union the busy state so the lock can never refuse an
  // action the UI still presents as available.
  const actionLock = useRef<"role" | "status" | "reset" | null>(null);
  const [actionSettling, setActionSettling] = useState(false);
  const [resetOwned, setResetOwned] = useState(false);
  const [actionError, setActionError] = useState<{
    action: "role" | "status";
    message: string;
    roleId?: number;
    desiredActive?: boolean;
  } | null>(null);
  /** value → label map for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const rowBusy =
    authority.blocked ||
    actionSettling ||
    roleMutation.isPending ||
    statusMutation.isPending ||
    resetOwned;
  const assignableRoles = roles.filter((role) => roleWithinCeiling(role, can, isOwner));
  const roleItems: Record<string, string> = {
    [NONE]: "No role",
    ...Object.fromEntries(roles.map((r) => [String(r.id), r.name])),
  };

  async function changeRole(roleId: number) {
    if (!authority.canStart() || actionLock.current !== null) return;
    actionLock.current = "role";
    setActionSettling(true);
    setActionError(null);
    const farmScope = captureFarmScope();
    try {
      await roleMutation.mutateAsync({ membershipId: m.id, data: { role_id: roleId } });
      if (!farmScope()) return;
      toast.success("Role updated.");
      // Keep the row claimed until the server-owned role/status snapshot has
      // landed. Unlocking after only the POST response exposes stale controls
      // (including controls the newly assigned role may make protected).
      await invalidate();
    } catch (err) {
      if (!farmScope()) return;
      const message = mutationError(err);
      setActionError({ action: "role", message, roleId });
      toast.error(message);
    } finally {
      actionLock.current = null;
      setActionSettling(false);
    }
  }

  const [confirmDeactivateOpen, setConfirmDeactivateOpen] = useState(false);

  async function setWorkerActive(desiredActive: boolean, confirmDeactivation = true) {
    if (!authority.canStart() || actionLock.current !== null) return;
    if (!desiredActive && m.is_active && confirmDeactivation) {
      setConfirmDeactivateOpen(true);
      return;
    }
    actionLock.current = "status";
    setActionSettling(true);
    setActionError(null);
    const farmScope = captureFarmScope();
    try {
      await statusMutation.mutateAsync({
        membershipId: m.id,
        data: { is_active: desiredActive },
      });
      if (!farmScope()) return;
      toast.success(desiredActive ? "Worker activated." : "Worker deactivated.");
      await invalidate();
    } catch (err) {
      if (!farmScope()) return;
      const message = mutationError(err);
      setActionError({ action: "status", message, desiredActive });
      toast.error(message);
    } finally {
      actionLock.current = null;
      setActionSettling(false);
    }
  }

  return (
    <TableRow>
      <TableCell>
        <div className="flex items-center gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            {(m.name ?? m.email).trim().charAt(0).toUpperCase()}
          </span>
          <span>{m.name ?? "—"}</span>
        </div>
      </TableCell>
      <TableCell>{m.email}</TableCell>
      <TableCell>
        <Select
          value={m.role_id !== null ? String(m.role_id) : NONE}
          onValueChange={(v) => {
            if (v === NONE) return;
            void changeRole(Number(v));
          }}
          disabled={rowBusy || isSelf || protectedTarget}
          items={roleItems}
        >
          <SelectTrigger
            size="sm"
            className="w-full"
            aria-label={`Role for ${m.name ?? m.email}`}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {m.role_id === null && (
              <SelectItem value={NONE} disabled>
                No role
              </SelectItem>
            )}
            {assignableRoles.map((r) => (
              <SelectItem key={r.id} value={String(r.id)}>
                {r.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {actionError?.action === "role" && (
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span role="alert" className="text-xs text-destructive">
              {actionError.message}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={rowBusy}
              onClick={() => {
                if (actionError.roleId !== undefined) void changeRole(actionError.roleId);
              }}
            >
              Retry role change
            </Button>
          </div>
        )}
      </TableCell>
      <TableCell>
        <StatusBadge status={m.is_active ? "ACTIVE" : "INACTIVE"} />
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-2">
          {isSelf ? (
            <span className="text-xs text-muted-foreground">
              Manage your password from Account.
            </span>
          ) : protectedTarget ? (
            <span className="text-xs text-muted-foreground">
              You can only manage workers whose current role stays within your own permissions.
            </span>
          ) : (
            <Button
              size="sm"
              variant="outline"
              disabled={rowBusy}
              onClick={() => void setWorkerActive(!m.is_active)}
            >
              {m.is_active ? "Deactivate" : "Activate"}
            </Button>
          )}
          {isOwner && !isSelf && !protectedTarget && (
            <div className="space-y-1">
              <Button
                size="sm"
                variant="outline"
                disabled={!m.can_reset_password || rowBusy}
                aria-describedby={
                  !m.can_reset_password ? `reset-password-reason-${m.id}` : undefined
                }
                onClick={() => {
                  if (
                    !m.can_reset_password ||
                    !authority.canStart() ||
                    actionLock.current !== null
                  ) return;
                  actionLock.current = "reset";
                  setResetOwned(true);
                  onReset(m, () => {
                    if (actionLock.current !== "reset") return;
                    actionLock.current = null;
                    setResetOwned(false);
                  });
                }}
              >
                Reset password
              </Button>
              {!m.can_reset_password && m.reset_password_block_reason && (
                <p
                  id={`reset-password-reason-${m.id}`}
                  className="max-w-64 text-xs text-muted-foreground"
                >
                  {m.reset_password_block_reason}
                </p>
              )}
            </div>
          )}
        </div>
        <Dialog
          open={confirmDeactivateOpen}
          onOpenChange={(nextOpen) => {
            if (!nextOpen && actionLock.current === "status") return;
            setConfirmDeactivateOpen(nextOpen);
          }}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Deactivate {m.name ?? m.email}?</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">
              They will immediately lose access to this farm. The membership and its audit
              history are retained and can be reactivated later.
            </p>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setConfirmDeactivateOpen(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="destructive"
                onClick={() => {
                  setConfirmDeactivateOpen(false);
                  void setWorkerActive(false, false);
                }}
              >
                Deactivate worker
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        {actionError?.action === "status" && (
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span role="alert" className="text-xs text-destructive">
              {actionError.message}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={rowBusy}
              onClick={() =>
                void setWorkerActive(actionError.desiredActive ?? !m.is_active, false)
              }
            >
              Retry {m.is_active ? "deactivate" : "activate"}
            </Button>
          </div>
        )}
      </TableCell>
    </TableRow>
  );
}

function AddWorkerDialog({
  open,
  onOpenChange,
  roles,
  authority,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roles: RoleOut[];
  authority: TeamAuthority;
}) {
  const invalidate = useInvalidateTeam();
  const createMutation = useCreateWorkerApiTeamWorkersPost();
  const createFlight = useSingleFlight();
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<WorkerValues>({
    resolver: zodResolver(workerSchema),
    defaultValues: { name: "", email: "", role_id: "", password: "" },
  });
  const wRoleId = useWatch({ control, name: "role_id" });
  /** value → label map for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const roleItems: Record<string, string> = Object.fromEntries(
    roles.map((r) => [String(r.id), `${r.name}${r.description ? ` — ${r.description}` : ""}`]),
  );

  async function onSubmit(values: WorkerValues) {
    if (!authority.canStart()) return;
    await createFlight.run(async () => {
      const farmScope = captureFarmScope();
      setFormError(null);
      try {
        await createMutation.mutateAsync({
          data: {
            email: values.email.trim(),
            name: values.name?.trim() || null,
            role_id: Number(values.role_id),
            password: values.password,
          },
        });
        if (!farmScope()) return;
        toast.success("Worker added.");
        await invalidate();
        onOpenChange(false);
        reset();
      } catch (err) {
        if (!farmScope()) return;
        const message = mutationError(err);
        setFormError(message);
        toast.error(message);
      }
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && (isSubmitting || createFlight.pending)) return;
        if (!nextOpen) {
          setFormError(null);
          // Dismissal (Esc/backdrop) must clear the form exactly like the
          // Cancel button: the dialog stays mounted for owners, and a
          // half-typed password must not survive into the next open (RT-P2-1).
          reset();
        }
        onOpenChange(nextOpen);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add worker</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          The worker logs in with this email and password and sees only what the selected role
          allows. This form creates a new worker account; an email that is already registered
          cannot be enrolled here.
        </p>
        <form onSubmit={handleSubmit(onSubmit)} noValidate>
          <fieldset
            disabled={authority.blocked || isSubmitting || createFlight.pending}
            className="space-y-4"
          >
          {formError && (
            <p role="alert" className="text-sm text-destructive">
              {formError}
            </p>
          )}
          {roles.length === 0 && (
            <p role="alert" className="text-sm text-destructive">
              You have no roles you are allowed to assign. Ask the farm owner to create or grant
              an assignable role first.
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="worker-name">Name</Label>
            <Input
              id="worker-name"
              maxLength={120}
              placeholder="e.g. Ravi Kumar"
              aria-invalid={Boolean(errors.name) || undefined}
              aria-describedby={errors.name ? "worker-name-error" : undefined}
              {...register("name")}
            />
            {errors.name && (
              <p id="worker-name-error" role="alert" className="text-sm text-destructive">
                {errors.name.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="worker-email">Email *</Label>
            <Input
              id="worker-email"
              type="email"
              maxLength={254}
              placeholder="worker@example.com"
              autoFocus
              aria-invalid={Boolean(errors.email) || undefined}
              aria-describedby={errors.email ? "worker-email-error" : undefined}
              {...register("email")}
            />
            {errors.email && (
              <p id="worker-email-error" role="alert" className="text-sm text-destructive">
                {errors.email.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="worker-password">Password (min 12 chars) *</Label>
            <Input
              id="worker-password"
              type="password"
              maxLength={128}
              autoComplete="new-password"
              aria-invalid={Boolean(errors.password) || undefined}
              aria-describedby={errors.password ? "worker-password-error" : undefined}
              {...register("password")}
            />
            {errors.password && (
              <p id="worker-password-error" role="alert" className="text-sm text-destructive">
                {errors.password.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="worker-role">Role *</Label>
            <Select value={wRoleId} onValueChange={(v) => setValue("role_id", v, { shouldValidate: true })} items={roleItems}>
              <SelectTrigger
                id="worker-role"
                className="w-full"
                aria-invalid={Boolean(errors.role_id) || undefined}
                aria-describedby={errors.role_id ? "worker-role-error" : undefined}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {roles.map((r) => (
                  <SelectItem key={r.id} value={String(r.id)}>
                    {r.name}
                    {r.description ? ` — ${r.description}` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errors.role_id && (
              <p id="worker-role-error" role="alert" className="text-sm text-destructive">
                {errors.role_id.message}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={isSubmitting || createFlight.pending}
              onClick={() => {
                // Cancel must also clear the form: the dialog stays mounted
                // for owners, and a half-typed password should not survive
                // into the next open.
                reset();
                onOpenChange(false);
              }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={
                authority.blocked ||
                isSubmitting ||
                createFlight.pending ||
                roles.length === 0
              }
            >
              {isSubmitting || createFlight.pending
                ? "Adding…"
                : formError
                  ? "Retry add worker"
                  : "Add worker"}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ResetPasswordDialog({
  membership,
  onClose,
  authority,
}: {
  membership: MembershipOut;
  onClose: () => void;
  authority: TeamAuthority;
}) {
  const resetMutation = useResetPasswordApiTeamWorkersMembershipIdResetPasswordPost();
  const resetFlight = useSingleFlight();
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ResetValues>({
    resolver: zodResolver(resetSchema),
    defaultValues: { password: "" },
  });

  async function onSubmit(values: ResetValues) {
    if (!authority.canStart()) return;
    await resetFlight.run(async () => {
      const farmScope = captureFarmScope();
      setFormError(null);
      try {
        await resetMutation.mutateAsync({
          membershipId: membership.id,
          data: { password: values.password },
        });
        if (!farmScope()) return;
        toast.success("Password reset.");
        onClose();
      } catch (err) {
        if (!farmScope()) return;
        const message = mutationError(err);
        setFormError(message);
        toast.error(message);
      }
    });
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && !isSubmitting && !resetFlight.pending && onClose()}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reset password — {membership.name ?? membership.email}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} noValidate>
          <fieldset
            disabled={authority.blocked || isSubmitting || resetFlight.pending}
            className="space-y-4"
          >
          {formError && (
            <p role="alert" className="text-sm text-destructive">
              {formError}
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="reset-password">New password (min 12 chars) *</Label>
            <Input
              id="reset-password"
              type="password"
              maxLength={128}
              autoComplete="new-password"
              autoFocus
              aria-invalid={Boolean(errors.password) || undefined}
              aria-describedby={errors.password ? "reset-password-error" : undefined}
              {...register("password")}
            />
            {errors.password && (
              <p id="reset-password-error" role="alert" className="text-sm text-destructive">
                {errors.password.message}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={isSubmitting || resetFlight.pending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={authority.blocked || isSubmitting || resetFlight.pending}
            >
              {isSubmitting || resetFlight.pending
                ? "Resetting…"
                : formError
                  ? "Retry password reset"
                  : "Reset password"}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Create/edit role dialog. Permissions the editor cannot grant remain
 * visible and, when already selected, are preserved in the update payload. */
function RoleDialog({
  role,
  team,
  can,
  isOwner,
  onClose,
  authority,
}: {
  role: RoleOut | null;
  team: TeamOut;
  can: (code: string) => boolean;
  isOwner: boolean;
  onClose: () => void;
  authority: TeamAuthority;
}) {
  const invalidate = useInvalidateTeam();
  const createMutation = useCreateRoleApiTeamRolesPost();
  const updateMutation = useUpdateRoleApiTeamRolesRoleIdPut();
  const saveFlight = useSingleFlight();
  const [formError, setFormError] = useState<string | null>(null);
  const canGrant = (code: string) => can(code) && (isOwner || code !== "team.manage");
  const [selected, setSelected] = useState<Set<string>>(
    () => {
      const initial = new Set(role?.permissions ?? []);
      // Repair an action-only role when this editor can grant its missing
      // view dependency. Dependencies they cannot grant stay untouched and
      // visibly flagged rather than being silently discarded.
      for (const code of initial) {
        const dependency = PERMISSION_DEPENDENCIES[code];
        if (dependency && canGrant(dependency)) initial.add(dependency);
      }
      return initial;
    },
  );
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RoleValues>({
    resolver: zodResolver(roleSchema),
    defaultValues: { name: role?.name ?? "", description: role?.description ?? "" },
  });

  function togglePerm(code: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(code);
        const dependency = PERMISSION_DEPENDENCIES[code];
        if (dependency && canGrant(dependency)) next.add(dependency);
      } else {
        next.delete(code);
      }
      return next;
    });
  }

  async function onSubmit(values: RoleValues) {
    if (!authority.canStart()) return;
    await saveFlight.run(async () => {
      const farmScope = captureFarmScope();
      setFormError(null);
      const data = {
        name: values.name.trim(),
        description: values.description?.trim() || null,
        permissions: [...selected],
      };
      try {
        if (role) {
          await updateMutation.mutateAsync({
            roleId: role.id,
            data: { ...data, expected_revision: role.revision },
          });
          if (!farmScope()) return;
          toast.success("Role saved.");
        } else {
          await createMutation.mutateAsync({ data });
          if (!farmScope()) return;
          toast.success("Role created.");
        }
        await invalidate();
        onClose();
      } catch (err) {
        if (!farmScope()) return;
        // A 409 means another admin already changed this role, so the
        // `expected_revision` closed over here is stale. Without refreshing
        // the cache a retry replays the same stale revision and is guaranteed
        // to 409 forever. The parent keys this dialog by the refreshed
        // revision, so invalidation remounts a truthful editor instead of
        // preserving stale permissions and overwriting the other admin.
        if (err instanceof ApiError && err.status === 409) await invalidate();
        const message = mutationError(err);
        setFormError(message);
        toast.error(message);
      }
    });
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && !isSubmitting && !saveFlight.pending && onClose()}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{role ? `Edit role: ${role.name}` : "New role"}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} noValidate>
          <fieldset
            disabled={authority.blocked || isSubmitting || saveFlight.pending}
            className="space-y-4"
          >
          {formError && (
            <p role="alert" className="text-sm text-destructive">
              {formError}
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="role-name">Role name *</Label>
            <Input
              id="role-name"
              maxLength={80}
              placeholder="e.g. Night Watchman"
              autoFocus
              aria-invalid={Boolean(errors.name) || undefined}
              aria-describedby={errors.name ? "role-name-error" : undefined}
              {...register("name")}
            />
            {errors.name && (
              <p id="role-name-error" role="alert" className="text-sm text-destructive">
                {errors.name.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="role-description">Description</Label>
            <Input
              id="role-description"
              maxLength={255}
              placeholder="what this role is responsible for"
              aria-invalid={Boolean(errors.description) || undefined}
              aria-describedby={errors.description ? "role-description-error" : undefined}
              {...register("description")}
            />
            {errors.description && (
              <p
                id="role-description-error"
                role="alert"
                className="text-sm text-destructive"
              >
                {errors.description.message}
              </p>
            )}
          </div>
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Permissions</h3>
            <p className="text-sm text-muted-foreground">
              Selecting an action also selects the view permission needed to reach it. Permissions
              you do not hold are shown disabled; existing checked ones are retained when you save.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {team.permission_groups.map((g) => (
                <fieldset key={g.group} className="space-y-1.5">
                  <legend className="text-sm font-medium">{g.group}</legend>
                  {g.codes.map((code) => {
                    const held = can(code);
                    const grantable = canGrant(code);
                    const checked = selected.has(code);
                    const dependency = PERMISSION_DEPENDENCIES[code];
                    const missingDependency = dependency ? !selected.has(dependency) : false;
                    const requiredBy = Object.entries(PERMISSION_DEPENDENCIES)
                      .filter(([action, view]) => view === code && selected.has(action))
                      .map(([action]) => action);
                    const disabled =
                      !grantable ||
                      (checked && requiredBy.length > 0) ||
                      (!checked && Boolean(dependency) && missingDependency && !canGrant(dependency));
                    const controlId = `permission-${code.replaceAll(".", "-")}`;
                    const labelId = `${controlId}-label`;
                    return (
                      <div
                        key={code}
                        className="flex items-start gap-2 text-sm data-[disabled=true]:text-muted-foreground"
                        data-disabled={disabled}
                      >
                        <Checkbox
                          id={controlId}
                          aria-labelledby={labelId}
                          checked={checked}
                          disabled={disabled}
                          onCheckedChange={(value) => togglePerm(code, value === true)}
                        />
                        <span>
                          <Label id={labelId} htmlFor={controlId} className="inline text-sm">
                            {team.permission_labels[code] ?? code}
                          </Label>
                          {!grantable && checked && (
                            <span className="ml-1 text-xs">(retained; you cannot change this)</span>
                          )}
                          {!grantable && !checked && (
                            <span className="ml-1 text-xs">
                              {held && code === "team.manage"
                                ? "(only the farm owner can grant this)"
                                : "(you cannot grant this)"}
                            </span>
                          )}
                          {missingDependency && (
                            <span className="block text-xs text-warning-tint-foreground">
                              Requires {team.permission_labels[dependency] ?? dependency}.
                            </span>
                          )}
                          {requiredBy.length > 0 && (
                            <span className="block text-xs text-muted-foreground">
                              Required by {requiredBy.map((action) => team.permission_labels[action] ?? action).join(", ")}.
                            </span>
                          )}
                        </span>
                      </div>
                    );
                  })}
                </fieldset>
              ))}
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={isSubmitting || saveFlight.pending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={authority.blocked || isSubmitting || saveFlight.pending}
            >
              {isSubmitting || saveFlight.pending
                ? "Saving…"
                : formError
                  ? role
                    ? "Retry save role"
                    : "Retry create role"
                  : role
                    ? "Save role"
                    : "Create role"}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RoleCard({
  role,
  team,
  canManageRole,
  onEdit,
  authority,
}: {
  role: RoleOut;
  team: TeamOut;
  canManageRole: boolean;
  onEdit: (role: RoleOut) => void;
  authority: TeamAuthority;
}) {
  const invalidate = useInvalidateTeam();
  const deleteMutation = useDeleteRoleApiTeamRolesRoleIdDelete();
  const deleteLock = useRef(false);
  const [deleteSettling, setDeleteSettling] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const memberCount = role.member_count ?? 0;
  const managerRole = role.permissions.includes("team.manage");
  const scopeHint = !canManageRole
    ? managerRole
      ? "Only the farm owner can edit or delete a team-manager role."
      : "You can only edit or delete roles whose permissions you also hold."
    : undefined;
  const deleteHint = scopeHint ?? (role.code
    ? "Preset roles can't be deleted."
    : memberCount > 0
      ? "Role still has workers assigned — reassign them first."
      : undefined);

  async function deleteRole() {
    if (!authority.canStart() || deleteLock.current || deleteHint !== undefined) return;
    deleteLock.current = true;
    setDeleteSettling(true);
    setDeleteError(null);
    const farmScope = captureFarmScope();
    try {
      await deleteMutation.mutateAsync({ roleId: role.id });
      if (!farmScope()) return;
      toast.success("Role deleted.");
      // The cached card survives while /api/team refetches. Hold both actions
      // through that refresh so the deleted role cannot be edited or deleted
      // again from the stale card.
      await invalidate();
    } catch (err) {
      if (!farmScope()) return;
      const message = mutationError(err);
      setDeleteError(message);
      toast.error(message);
    } finally {
      deleteLock.current = false;
      setDeleteSettling(false);
    }
  }

  function editRole() {
    // The ref closes the same-render gap before deleteSettling can paint.
    if (
      !authority.canStart() ||
      deleteLock.current ||
      deleteSettling ||
      deleteMutation.isPending
    ) return;
    onEdit(role);
  }

  return (
    <Card className="gap-3 p-4">
      <div className="flex flex-col items-start justify-between gap-3 sm:flex-row">
        <div>
          <span className="font-medium">{role.name}</span>{" "}
          {role.code && (
            <Badge variant="secondary">
              <Lock aria-hidden="true" className="size-3" />
              Preset
            </Badge>
          )}
          <div className="text-sm text-muted-foreground">{role.description ?? "—"}</div>
          <div className="text-xs text-muted-foreground">
            {memberCount} member{memberCount === 1 ? "" : "s"}
            {role.code && " · built in — edits allowed, deletion not"}
          </div>
        </div>
        <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:shrink-0">
          <Button
            size="sm"
            variant="outline"
            disabled={
              authority.blocked ||
              scopeHint !== undefined ||
              deleteSettling ||
              deleteMutation.isPending
            }
            title={scopeHint}
            onClick={editRole}
          >
            Edit
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={
              authority.blocked ||
              deleteHint !== undefined ||
              deleteSettling ||
              deleteMutation.isPending
            }
            title={deleteHint}
            onClick={() => setConfirmDeleteOpen(true)}
          >
            Delete
          </Button>
        </div>
      </div>
      <Dialog
        open={confirmDeleteOpen}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && (deleteLock.current || deleteMutation.isPending)) return;
          setConfirmDeleteOpen(nextOpen);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete role &quot;{role.name}&quot;?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Workers holding only this role lose their assignment. The action cannot be undone.
          </p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmDeleteOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => {
                setConfirmDeleteOpen(false);
                void deleteRole();
              }}
            >
              Delete role
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {deleteError && (
        <div className="flex flex-wrap items-center gap-2">
          <span role="alert" className="text-xs text-destructive">
            {deleteError}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={authority.blocked || deleteSettling || deleteMutation.isPending}
            onClick={() => void deleteRole()}
          >
            Retry delete role
          </Button>
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {role.permissions.length === 0 ? (
          <span className="text-xs text-muted-foreground">No permissions.</span>
        ) : (
          role.permissions.map((code) => (
            <Badge key={code} variant="secondary">
              {team.permission_labels[code] ?? code}
            </Badge>
          ))
        )}
      </div>
    </Card>
  );
}

function TeamPageContent({ perms }: { perms: PermissionsState }) {
  const { user } = useAuth();
  const { can, isOwner } = perms;
  const allowed = can("team.manage");
  const queryClient = useQueryClient();

  const [workerOpen, setWorkerOpen] = useState(false);
  type ResetTarget = {
    membership: MembershipOut;
    release: () => void;
  };
  const [resetTarget, setResetTarget] = useState<ResetTarget | null>(null);
  const resetTargetRef = useRef<ResetTarget | null>(null);
  const [roleDialog, setRoleDialog] = useState<{ role: RoleOut | null } | null>(null);

  const query = useTeamPageApiTeamGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const authority: TeamAuthority = {
    blocked: query.fetchStatus !== "idle" || query.isError,
    // Read the cache synchronously as well as disabling painted controls.
    // TanStack batches observer renders, so another click can otherwise land
    // after invalidateQueries starts a replacement GET but before React shows
    // query.isFetching. A failed background GET also leaves cached rows on
    // screen; status=error keeps those stale controls inert until retry wins.
    canStart: () => {
      const state = queryClient.getQueryState(getTeamPageApiTeamGetQueryKey());
      return state?.status === "success" && state.fetchStatus === "idle";
    },
  };

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div className="space-y-3" role="alert">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : "Could not load the team."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry team
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
        title="Team"
        description="Manage the workers on this farm, their roles and what each role can do."
      />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading the team…</span>
          <PageSkeleton cards={2} />
        </div>
      </div>
    );
  }

  const roles = payload.roles;
  const assignableRoles = roles.filter(
    (role) => roleWithinCeiling(role, can, isOwner),
  );
  // The dialog state records which role was opened, while the query payload
  // owns its current revision. A 409 invalidation must therefore remount the
  // editor from the refreshed role instead of retrying the stale revision (or
  // preserving stale permissions and overwriting a concurrent admin).
  const currentDialogRole = roleDialog?.role
    ? payload.roles.find((role) => role.id === roleDialog.role?.id)
    : null;
  function isProtectedTarget(membership: MembershipOut): boolean {
    if (isOwner) return false;
    const role = roles.find((candidate) => candidate.id === membership.role_id);
    return role ? !roleWithinCeiling(role, can, isOwner) : false;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Team"
        description="Manage the workers on this farm, their roles and what each role can do."
      />

      {/* Deliberately NOT the shared StaleDataNotice: stale team data is not
          just informational — authority checks freeze every action until a
          fresh snapshot lands, so this banner is an assertive alert that
          blocks work rather than an informational status. Keep the two
          contracts separate. */}
      {query.isError && (
        <div className="space-y-3" role="alert">
          <p className="text-sm text-destructive">
            Team data could not be refreshed. Actions are disabled until the latest team snapshot
            loads.
          </p>
          <Button
            type="button"
            variant="outline"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            Retry team refresh
          </Button>
        </div>
      )}

      {authority.blocked && !query.isError && (
        <p className="text-sm text-muted-foreground" role="status">
          Waiting for the latest team data… actions will be available when it finishes.
        </p>
      )}

      <DataTableCard
        title="Workers"
        description={
          isOwner
            ? "People who can sign in to this farm."
            : "People who can sign in to this farm. Only the farm owner can create worker accounts."
        }
        actions={
          // POST /api/team/workers is owner-only, so offering the dialog to a
          // delegated team.manage holder can only ever end in a 403.
          isOwner ? (
            <Button
              disabled={authority.blocked}
              onClick={() => {
                if (!authority.canStart()) return;
                setWorkerOpen(true);
              }}
            >
              Add worker
            </Button>
          ) : undefined
        }
      >
        {payload.memberships.length === 0 ? (
          <EmptyState
            icon={Users}
            title="No workers yet"
            description="Add your first worker — they'll see only what their role allows."
          >
            {isOwner && (
              <Button
                variant="outline"
                size="sm"
                disabled={authority.blocked}
                onClick={() => {
                  if (!authority.canStart()) return;
                  setWorkerOpen(true);
                }}
              >
                Add worker
              </Button>
            )}
          </EmptyState>
        ) : (
          <Table className="min-w-[760px]">
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead><span className="sr-only">Actions</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.memberships.map((m) => (
                <WorkerRow
                  key={m.id}
                  m={m}
                  roles={payload.roles}
                  can={can}
                  isSelf={m.email === user?.email}
                  isOwner={isOwner}
                  protectedTarget={isProtectedTarget(m)}
                  authority={authority}
                  onReset={(membership, release) => {
                    resetTargetRef.current?.release();
                    const target = { membership, release };
                    resetTargetRef.current = target;
                    setResetTarget(target);
                  }}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </DataTableCard>

      <DataTableCard
        title="Roles"
        description={
          <>
            A role bundles the pages and actions a worker can use. Preset roles are built in —
            edit them, but they can&apos;t be deleted.
          </>
        }
        actions={
          <Button
            variant="outline"
            disabled={authority.blocked}
            onClick={() => {
              if (authority.canStart()) setRoleDialog({ role: null });
            }}
          >
            New role
          </Button>
        }
      >
        {payload.roles.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="No roles yet."
            description="Create a role to control what workers can see and do."
          >
            <Button
              variant="outline"
              size="sm"
              disabled={authority.blocked}
              onClick={() => {
                if (authority.canStart()) setRoleDialog({ role: null });
              }}
            >
              Create a role
            </Button>
          </EmptyState>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {payload.roles.map((r) => (
              <RoleCard
                key={r.id}
                role={r}
                team={payload}
                canManageRole={roleWithinCeiling(r, can, isOwner)}
                onEdit={(role) => setRoleDialog({ role })}
                authority={authority}
              />
            ))}
          </div>
        )}
      </DataTableCard>

      {isOwner && (
        <AddWorkerDialog
          open={workerOpen}
          onOpenChange={setWorkerOpen}
          roles={assignableRoles}
          authority={authority}
        />
      )}
      {resetTarget && (
        <ResetPasswordDialog
          key={resetTarget.membership.id}
          membership={resetTarget.membership}
          authority={authority}
          onClose={() => {
            const closingTarget = resetTarget;
            if (resetTargetRef.current === closingTarget) {
              closingTarget.release();
              resetTargetRef.current = null;
            }
            setResetTarget((current) =>
              current === closingTarget ? null : current,
            );
          }}
        />
      )}
      {roleDialog && (roleDialog.role === null || currentDialogRole) && (
        <RoleDialog
          key={
            currentDialogRole
              ? `${currentDialogRole.id}:${currentDialogRole.revision}`
              : "new"
          }
          role={currentDialogRole ?? null}
          team={payload}
          can={can}
          isOwner={isOwner}
          onClose={() => setRoleDialog(null)}
          authority={authority}
        />
      )}
    </div>
  );
}

export default function TeamPage() {
  const perms = usePermissions();
  return (
    <PermissionGate
      perms={perms}
      perm="team.manage"
      label="Team"
      description="Manage the workers on this farm, their roles and what each role can do."
      cards={2}
      announce
    >
      <TeamPageContent perms={perms} />
    </PermissionGate>
  );
}

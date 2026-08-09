"use client";

/** Team management: workers, roles, permission matrix — parity with v1's team/list.html + worker_new.html + role_form.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, Users } from "lucide-react";
import { useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
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
import { useAuth } from "@/lib/auth-context";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

/** Sentinel for "no role" (empty string is not a valid item value). */
const NONE = "none";

function mutationError(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

function useInvalidateTeam() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: getTeamPageApiTeamGetQueryKey() });
}

const workerSchema = z.object({
  name: z.string().trim().max(120).optional(),
  email: z.string().trim().email("Enter a valid email address"),
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
}: {
  m: MembershipOut;
  roles: RoleOut[];
  can: (code: string) => boolean;
  isSelf: boolean;
  protectedTarget: boolean;
  isOwner: boolean;
  onReset: (m: MembershipOut) => void;
}) {
  const invalidate = useInvalidateTeam();
  const roleMutation = useChangeRoleApiTeamWorkersMembershipIdRolePost();
  const statusMutation = useSetWorkerStatusApiTeamWorkersMembershipIdStatusPut();
  const actionLock = useRef<"role" | "status" | null>(null);
  const [actionError, setActionError] = useState<{
    action: "role" | "status";
    message: string;
    roleId?: number;
    desiredActive?: boolean;
  } | null>(null);
  /** value → label map for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const assignableRoles = roles.filter((role) => roleWithinCeiling(role, can, isOwner));
  const roleItems: Record<string, string> = {
    [NONE]: "No role",
    ...Object.fromEntries(roles.map((r) => [String(r.id), r.name])),
  };

  async function changeRole(roleId: number) {
    if (actionLock.current !== null) return;
    actionLock.current = "role";
    setActionError(null);
    try {
      await roleMutation.mutateAsync({ membershipId: m.id, data: { role_id: roleId } });
      toast.success("Role updated.");
      invalidate();
    } catch (err) {
      const message = mutationError(err);
      setActionError({ action: "role", message, roleId });
      toast.error(message);
    } finally {
      actionLock.current = null;
    }
  }

  async function setWorkerActive(desiredActive: boolean, confirmDeactivation = true) {
    if (actionLock.current !== null) return;
    if (
      !desiredActive &&
      m.is_active &&
      confirmDeactivation &&
      !window.confirm(`Deactivate ${m.name ?? m.email}? They will immediately lose farm access.`)
    ) {
      return;
    }
    actionLock.current = "status";
    setActionError(null);
    try {
      await statusMutation.mutateAsync({
        membershipId: m.id,
        data: { is_active: desiredActive },
      });
      toast.success(desiredActive ? "Worker activated." : "Worker deactivated.");
      invalidate();
    } catch (err) {
      const message = mutationError(err);
      setActionError({ action: "status", message, desiredActive });
      toast.error(message);
    } finally {
      actionLock.current = null;
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
          disabled={roleMutation.isPending || isSelf || protectedTarget}
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
              disabled={roleMutation.isPending}
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
              disabled={statusMutation.isPending}
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
                disabled={!m.can_reset_password}
                aria-describedby={
                  !m.can_reset_password ? `reset-password-reason-${m.id}` : undefined
                }
                onClick={() => m.can_reset_password && onReset(m)}
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
        {actionError?.action === "status" && (
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span role="alert" className="text-xs text-destructive">
              {actionError.message}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={statusMutation.isPending}
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
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roles: RoleOut[];
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
    await createFlight.run(async () => {
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
        toast.success("Worker added.");
        invalidate();
        onOpenChange(false);
        reset();
      } catch (err) {
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
        if (!nextOpen) setFormError(null);
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
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
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
              type="submit"
              disabled={isSubmitting || createFlight.pending || roles.length === 0}
            >
              {isSubmitting || createFlight.pending
                ? "Adding…"
                : formError
                  ? "Retry add worker"
                  : "Add worker"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ResetPasswordDialog({
  membership,
  onClose,
}: {
  membership: MembershipOut;
  onClose: () => void;
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
    await resetFlight.run(async () => {
      setFormError(null);
      try {
        await resetMutation.mutateAsync({
          membershipId: membership.id,
          data: { password: values.password },
        });
        toast.success("Password reset.");
        onClose();
      } catch (err) {
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
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
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
            <Button type="submit" disabled={isSubmitting || resetFlight.pending}>
              {isSubmitting || resetFlight.pending
                ? "Resetting…"
                : formError
                  ? "Retry password reset"
                  : "Reset password"}
            </Button>
          </DialogFooter>
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
}: {
  role: RoleOut | null;
  team: TeamOut;
  can: (code: string) => boolean;
  isOwner: boolean;
  onClose: () => void;
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
    await saveFlight.run(async () => {
      setFormError(null);
      const data = {
        name: values.name.trim(),
        description: values.description?.trim() || null,
        permissions: [...selected],
      };
      try {
        if (role) {
          await updateMutation.mutateAsync({ roleId: role.id, data });
          toast.success("Role saved.");
        } else {
          await createMutation.mutateAsync({ data });
          toast.success("Role created.");
        }
        invalidate();
        onClose();
      } catch (err) {
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
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
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
                            <span className="block text-xs text-amber-700 dark:text-amber-300">
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
            <Button type="submit" disabled={isSubmitting || saveFlight.pending}>
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
}: {
  role: RoleOut;
  team: TeamOut;
  canManageRole: boolean;
  onEdit: (role: RoleOut) => void;
}) {
  const invalidate = useInvalidateTeam();
  const deleteMutation = useDeleteRoleApiTeamRolesRoleIdDelete();
  const deleteLock = useRef(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
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
    if (deleteLock.current || deleteHint !== undefined) return;
    if (!window.confirm(`Delete role "${role.name}"?`)) return;
    deleteLock.current = true;
    setDeleteError(null);
    try {
      await deleteMutation.mutateAsync({ roleId: role.id });
      toast.success("Role deleted.");
      invalidate();
    } catch (err) {
      const message = mutationError(err);
      setDeleteError(message);
      toast.error(message);
    } finally {
      deleteLock.current = false;
    }
  }

  return (
    <Card className="gap-3 p-4">
      <div className="flex flex-col items-start justify-between gap-3 sm:flex-row">
        <div>
          <span className="font-medium">{role.name}</span>{" "}
          {role.code && (
            <Badge
              variant="outline"
              className="border-transparent bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
            >
              preset
            </Badge>
          )}
          <div className="text-sm text-muted-foreground">{role.description ?? "—"}</div>
          <div className="text-xs text-muted-foreground">
            {memberCount} member{memberCount === 1 ? "" : "s"}
          </div>
        </div>
        <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:shrink-0">
          <Button
            size="sm"
            variant="outline"
            disabled={scopeHint !== undefined}
            title={scopeHint}
            onClick={() => onEdit(role)}
          >
            Edit
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={deleteHint !== undefined || deleteMutation.isPending}
            title={deleteHint}
            onClick={() => void deleteRole()}
          >
            Delete
          </Button>
        </div>
      </div>
      {deleteError && (
        <div className="flex flex-wrap items-center gap-2">
          <span role="alert" className="text-xs text-destructive">
            {deleteError}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={deleteMutation.isPending}
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

export default function TeamPage() {
  const { user } = useAuth();
  const { can, isOwner, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("team.manage");

  const [workerOpen, setWorkerOpen] = useState(false);
  const [resetTarget, setResetTarget] = useState<MembershipOut | null>(null);
  const [roleDialog, setRoleDialog] = useState<{ role: RoleOut | null } | null>(null);

  const query = useTeamPageApiTeamGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  if (permsLoading) {
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }
  if (permsError) {
    return (
      <p className="text-sm text-destructive">
        Could not load your permissions — refresh the page to try again.
      </p>
    );
  }
  if (!allowed) {
    return <p className="text-muted-foreground">You don&apos;t have access to this page.</p>;
  }
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
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  const assignableRoles = payload.roles.filter(
    (role) => roleWithinCeiling(role, can, isOwner),
  );
  function isProtectedTarget(membership: MembershipOut): boolean {
    if (isOwner) return false;
    const role = payload!.roles.find((candidate) => candidate.id === membership.role_id);
    return role ? !roleWithinCeiling(role, can, isOwner) : false;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Team"
        description="Manage the workers on this farm, their roles and what each role can do."
      />

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
              onClick={() => {
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
          />
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
                  onReset={setResetTarget}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </DataTableCard>

      <DataTableCard
        title="Roles"
        description="A role bundles the pages and actions a worker can use."
        actions={<Button onClick={() => setRoleDialog({ role: null })}>New role</Button>}
      >
        {payload.roles.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="No roles yet."
            description="Create a role to control what workers can see and do."
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {payload.roles.map((r) => (
              <RoleCard
                key={r.id}
                role={r}
                team={payload}
                canManageRole={roleWithinCeiling(r, can, isOwner)}
                onEdit={(role) => setRoleDialog({ role })}
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
        />
      )}
      {resetTarget && (
        <ResetPasswordDialog
          key={resetTarget.id}
          membership={resetTarget}
          onClose={() => setResetTarget(null)}
        />
      )}
      {roleDialog && (
        <RoleDialog
          key={roleDialog.role?.id ?? "new"}
          role={roleDialog.role}
          team={payload}
          can={can}
          isOwner={isOwner}
          onClose={() => setRoleDialog(null)}
        />
      )}
    </div>
  );
}

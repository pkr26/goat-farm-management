"use client";

/** Team management: workers, roles, permission matrix — parity with v1's team/list.html + worker_new.html + role_form.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, Users } from "lucide-react";
import { useState } from "react";
import { useForm , useWatch} from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getTeamPageApiTeamGetQueryKey,
  useChangeRoleApiTeamWorkersMembershipIdRolePost,
  useCreateRoleApiTeamRolesPost,
  useCreateWorkerApiTeamWorkersPost,
  useDeleteRoleApiTeamRolesRoleIdDelete,
  useResetPasswordApiTeamWorkersMembershipIdResetPasswordPost,
  useTeamPageApiTeamGet,
  useToggleWorkerApiTeamWorkersMembershipIdTogglePost,
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
    .refine((s) => s === "" || s.length >= 8, "Password must be at least 8 characters")
    .optional(),
});
type WorkerValues = z.infer<typeof workerSchema>;

const resetSchema = z.object({
  password: z.string().min(8, "Password must be at least 8 characters"),
});
type ResetValues = z.infer<typeof resetSchema>;

const roleSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(80),
  description: z.string().trim().max(255).optional(),
});
type RoleValues = z.infer<typeof roleSchema>;

/** Per-row worker controls: role reassign, activate/deactivate (never for self), reset password. */
function WorkerRow({
  m,
  roles,
  isSelf,
  onReset,
}: {
  m: MembershipOut;
  roles: RoleOut[];
  isSelf: boolean;
  onReset: (m: MembershipOut) => void;
}) {
  const invalidate = useInvalidateTeam();
  const roleMutation = useChangeRoleApiTeamWorkersMembershipIdRolePost();
  const toggleMutation = useToggleWorkerApiTeamWorkersMembershipIdTogglePost();
  /** value → label map for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const roleItems: Record<string, string> = {
    [NONE]: "No role",
    ...Object.fromEntries(roles.map((r) => [String(r.id), r.name])),
  };

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
            roleMutation
              .mutateAsync({ membershipId: m.id, data: { role_id: Number(v) } })
              .then(() => {
                toast.success("Role updated.");
                invalidate();
              })
              .catch((err) => toast.error(mutationError(err)));
          }}
          disabled={roleMutation.isPending}
          items={roleItems}
        >
          <SelectTrigger size="sm" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {m.role_id === null && (
              <SelectItem value={NONE} disabled>
                No role
              </SelectItem>
            )}
            {roles.map((r) => (
              <SelectItem key={r.id} value={String(r.id)}>
                {r.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </TableCell>
      <TableCell>
        <StatusBadge status={m.is_active ? "ACTIVE" : "INACTIVE"} />
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-2">
          {isSelf ? (
            <span className="text-xs text-muted-foreground">you</span>
          ) : (
            <Button
              size="sm"
              variant="outline"
              disabled={toggleMutation.isPending}
              onClick={() =>
                toggleMutation
                  .mutateAsync({ membershipId: m.id })
                  .then(() => {
                    toast.success(m.is_active ? "Worker deactivated." : "Worker activated.");
                    invalidate();
                  })
                  .catch((err) => toast.error(mutationError(err)))
              }
            >
              {m.is_active ? "Deactivate" : "Activate"}
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => onReset(m)}>
            Reset password
          </Button>
        </div>
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
    setFormError(null);
    try {
      await createMutation.mutateAsync({
        data: {
          email: values.email.trim(),
          name: values.name?.trim() || null,
          role_id: Number(values.role_id),
          // Blank password → backend only requires one when the email is a new account.
          password: values.password ? values.password : null,
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
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add worker</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          The worker logs in with this email and password and sees only what the selected role
          allows. If the email already has an account, it is linked to this farm and the password
          field is ignored.
        </p>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          {formError && <p className="text-sm text-destructive">{formError}</p>}
          <div className="space-y-1.5">
            <Label htmlFor="worker-name">Name</Label>
            <Input id="worker-name" maxLength={120} placeholder="e.g. Ravi Kumar" {...register("name")} />
            {errors.name && <p className="text-sm text-destructive">{errors.name.message}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="worker-email">Email *</Label>
            <Input id="worker-email" type="email" placeholder="worker@example.com" {...register("email")} />
            {errors.email && <p className="text-sm text-destructive">{errors.email.message}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="worker-password">Password (min 8 chars)</Label>
            <Input
              id="worker-password"
              type="password"
              placeholder="only for new accounts"
              {...register("password")}
            />
            {errors.password && (
              <p className="text-sm text-destructive">{errors.password.message}</p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="worker-role">Role *</Label>
            <Select value={wRoleId} onValueChange={(v) => setValue("role_id", v, { shouldValidate: true })} items={roleItems}>
              <SelectTrigger id="worker-role" className="w-full">
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
            {errors.role_id && <p className="text-sm text-destructive">{errors.role_id.message}</p>}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Adding…" : "Add worker"}
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
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reset password — {membership.name ?? membership.email}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          {formError && <p className="text-sm text-destructive">{formError}</p>}
          <div className="space-y-1.5">
            <Label htmlFor="reset-password">New password (min 8 chars) *</Label>
            <Input id="reset-password" type="password" {...register("password")} />
            {errors.password && (
              <p className="text-sm text-destructive">{errors.password.message}</p>
            )}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Resetting…" : "Reset password"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Create/edit role dialog with grouped permission matrix, clamped to permissions the editor holds. */
function RoleDialog({
  role,
  team,
  can,
  onClose,
}: {
  role: RoleOut | null;
  team: TeamOut;
  can: (code: string) => boolean;
  onClose: () => void;
}) {
  const invalidate = useInvalidateTeam();
  const createMutation = useCreateRoleApiTeamRolesPost();
  const updateMutation = useUpdateRoleApiTeamRolesRoleIdPut();
  const [formError, setFormError] = useState<string | null>(null);
  // Backend clamps to permissions the editor holds (_clean_permissions); mirror it here.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set((role?.permissions ?? []).filter(can)),
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
      if (checked) next.add(code);
      else next.delete(code);
      return next;
    });
  }

  async function onSubmit(values: RoleValues) {
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
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{role ? `Edit role: ${role.name}` : "New role"}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          {formError && <p className="text-sm text-destructive">{formError}</p>}
          <div className="space-y-1.5">
            <Label htmlFor="role-name">Role name *</Label>
            <Input id="role-name" maxLength={80} placeholder="e.g. Night Watchman" {...register("name")} />
            {errors.name && <p className="text-sm text-destructive">{errors.name.message}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="role-description">Description</Label>
            <Input
              id="role-description"
              maxLength={255}
              placeholder="what this role is responsible for"
              {...register("description")}
            />
            {errors.description && (
              <p className="text-sm text-destructive">{errors.description.message}</p>
            )}
          </div>
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Permissions</h3>
            <p className="text-sm text-muted-foreground">
              Workers with this role can open only the pages and actions ticked here. You can only
              grant permissions you hold yourself.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {team.permission_groups.map((g) => {
                const grantable = g.codes.filter(can);
                if (grantable.length === 0) return null;
                return (
                  <fieldset key={g.group} className="space-y-1.5">
                    <legend className="text-sm font-medium">{g.group}</legend>
                    {grantable.map((code) => (
                      <label key={code} className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={selected.has(code)}
                          onCheckedChange={(c) => togglePerm(code, c === true)}
                        />
                        {team.permission_labels[code] ?? code}
                      </label>
                    ))}
                  </fieldset>
                );
              })}
            </div>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : role ? "Save role" : "Create role"}
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
  onEdit,
}: {
  role: RoleOut;
  team: TeamOut;
  onEdit: (role: RoleOut) => void;
}) {
  const invalidate = useInvalidateTeam();
  const deleteMutation = useDeleteRoleApiTeamRolesRoleIdDelete();
  const memberCount = role.member_count ?? 0;
  const deleteHint = role.code
    ? "Preset roles can't be deleted."
    : memberCount > 0
      ? "Role still has workers assigned — reassign them first."
      : undefined;

  return (
    <Card className="gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
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
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="outline" onClick={() => onEdit(role)}>
            Edit
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={deleteHint !== undefined || deleteMutation.isPending}
            title={deleteHint}
            onClick={() => {
              if (!window.confirm(`Delete role "${role.name}"?`)) return;
              deleteMutation
                .mutateAsync({ roleId: role.id })
                .then(() => {
                  toast.success("Role deleted.");
                  invalidate();
                })
                .catch((err) => toast.error(mutationError(err)));
            }}
          >
            Delete
          </Button>
        </div>
      </div>
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
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
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
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError ? query.error.detail : "Could not load the team."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Team"
        description="Manage the workers on this farm, their roles and what each role can do."
      />

      <DataTableCard
        title="Workers"
        description="People who can sign in to this farm."
        actions={
          <Button
            onClick={() => {
              setWorkerOpen(true);
            }}
          >
            Add worker
          </Button>
        }
      >
        {payload.memberships.length === 0 ? (
          <EmptyState
            icon={Users}
            title="No workers yet"
            description="Add your first worker — they'll see only what their role allows."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.memberships.map((m) => (
                <WorkerRow
                  key={m.id}
                  m={m}
                  roles={payload.roles}
                  isSelf={m.email === user?.email}
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
              <RoleCard key={r.id} role={r} team={payload} onEdit={(role) => setRoleDialog({ role })} />
            ))}
          </div>
        )}
      </DataTableCard>

      <AddWorkerDialog open={workerOpen} onOpenChange={setWorkerOpen} roles={payload.roles} />
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
          onClose={() => setRoleDialog(null)}
        />
      )}
    </div>
  );
}

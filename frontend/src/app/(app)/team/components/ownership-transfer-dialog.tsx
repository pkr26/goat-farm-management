"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { getPermissionsApiAuthPermissionsGetQueryKey, useTransferFarmOwnershipApiAuthFarmsFarmIdTransferOwnershipPost } from "@/api/generated/endpoints";
import type { MembershipOut } from "@/api/generated/models";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiError, authSessionEpochValue, currentRequestScope } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { useT } from "@/lib/i18n";
import { useMutationError } from "@/lib/mutations";
import { useSingleFlight } from "@/lib/use-single-flight";

/** The server verifies password possession/rotation under its ownership lock.
 * A missing PIN flag is unknown, so legacy roster snapshots cannot establish
 * recipient eligibility. No credential or operational data is copied. */
export function OwnershipTransferDialog({ memberships, isOwner, blocked, canStart }: {
  memberships: MembershipOut[]; isOwner: boolean; blocked: boolean; canStart: () => boolean;
}) {
  const { farmId, user, refreshFarms } = useAuth();
  const queryClient = useQueryClient();
  const router = useRouter();
  const t = useT();
  const mutationError = useMutationError();
  const mutation = useTransferFarmOwnershipApiAuthFarmsFarmIdTransferOwnershipPost();
  const flight = useSingleFlight();
  const [open, setOpen] = useState(false);
  const [recipient, setRecipient] = useState("");
  const [password, setPassword] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eligible = memberships.filter((member) => member.is_active && member.pin_set === false && member.user_id !== user?.id);
  const items = Object.fromEntries(eligible.map((member) => [String(member.id), `${member.name || member.email} — ${member.email}`]));

  function dismiss() {
    if (flight.pending) return;
    setOpen(false); setRecipient(""); setPassword(""); setConfirmed(false); setError(null);
  }

  async function transfer() {
    if (!isOwner || farmId === null || !canStart() || !confirmed || !password || !eligible.some((member) => String(member.id) === recipient)) return;
    const requestedFarmId = farmId;
    await flight.run(async () => {
      const farmScope = captureFarmScope();
      const sessionEpoch = authSessionEpochValue();
      setError(null);
      try {
        const response = await mutation.mutateAsync({ farmId: requestedFarmId, data: { membership_id: Number(recipient), current_password: password } });
        if (response.status !== 200 || !farmScope() || sessionEpoch !== authSessionEpochValue()) return;
        setPassword("");
        setConfirmed(false);
        // Refreshing can intentionally remove this farm from the former
        // owner's list. Check session identity and unrelated farm switches,
        // rather than treating our own revocation as a stale continuation.
        await refreshFarms();
        if (sessionEpoch !== authSessionEpochValue()) return;
        const selectedFarm = currentRequestScope()?.farmScope ?? null;
        if (selectedFarm !== null && selectedFarm !== String(requestedFarmId)) return;
        await queryClient.invalidateQueries({ queryKey: getPermissionsApiAuthPermissionsGetQueryKey() });
        if (sessionEpoch !== authSessionEpochValue()) return;
        toast.success(t("team.transfer.success"));
        setOpen(false);
        setRecipient("");
        router.replace("/farm-select");
      } catch (cause) {
        if (!farmScope() || sessionEpoch !== authSessionEpochValue()) return;
        const detail = cause instanceof ApiError && cause.status === 409 ? cause.detail : mutationError(cause);
        setError(detail); toast.error(detail);
      }
    });
  }

  if (!isOwner) return null;
  return <>
    <Button variant="outline" disabled={blocked || flight.pending} onClick={() => { if (canStart()) setOpen(true); }}>{t("team.transfer.action")}</Button>
    <Dialog open={open} onOpenChange={(next) => { if (!next) dismiss(); }}>
      <DialogContent className="sm:max-w-lg"><DialogHeader>
        <DialogTitle>{t("team.transfer.title")}</DialogTitle>
        <DialogDescription>{t("team.transfer.description")}</DialogDescription>
      </DialogHeader>
        <form onSubmit={(event) => { event.preventDefault(); void transfer(); }}>
          <fieldset className="space-y-4" disabled={blocked || flight.pending}>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
            {eligible.length === 0 && <p role="status" className="text-sm text-muted-foreground">{t("team.transfer.noRecipients")}</p>}
            <div className="space-y-1.5"><Label htmlFor="transfer-recipient">{t("team.transfer.recipient")}</Label>
              <Select value={recipient || null} onValueChange={setRecipient} items={items}>
                <SelectTrigger id="transfer-recipient" className="w-full">
                  <SelectValue placeholder={t("team.transfer.recipientPlaceholder")} />
                </SelectTrigger>
                <SelectContent>{eligible.map((member) => <SelectItem key={member.id} value={String(member.id)}>{items[String(member.id)]}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5"><Label htmlFor="transfer-password">{t("team.transfer.password")}</Label>
              <Input id="transfer-password" type="password" value={password} maxLength={128} autoComplete="current-password" required onChange={(event) => setPassword(event.target.value)} />
            </div>
            <label className="flex items-start gap-2 text-sm"><Checkbox checked={confirmed} onCheckedChange={(value) => setConfirmed(value === true)} />{t("team.transfer.confirm")}</label>
            <DialogFooter><Button type="button" variant="outline" onClick={dismiss}>{t("common.cancel")}</Button>
              <Button type="submit" variant="destructive" disabled={!confirmed || !password || !eligible.some((member) => String(member.id) === recipient)}>{t(flight.pending ? "team.transfer.transferring" : "team.transfer.action")}</Button></DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  </>;
}

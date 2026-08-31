"use client";

/** Active farm's species type (GOAT default: legacy list payloads and
 * membership-only views may lack the field until refresh). */

import { useAuth } from "@/lib/auth-context";
import type { FarmType } from "@/lib/farm-vocabulary";

export function useFarmType(): FarmType {
  const { farms, farmId } = useAuth();
  const farm = farms.find((entry) => entry.id === farmId);
  const farmType = (farm as { farm_type?: string } | undefined)?.farm_type;
  return farmType === "BUFFALO_DAIRY" ? "BUFFALO_DAIRY" : "GOAT";
}

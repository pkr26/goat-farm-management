"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { APP_NAME } from "@/lib/brand";
import { useT, type MessageKey } from "@/lib/i18n";

/** Most-specific routes come first so nested tools are not titled as their
 * parent module. This table covers every page route, including public and
 * worker-only surfaces that do not mount the authenticated shell. */
const ROUTE_TITLES: [RegExp, MessageKey][] = [
  [/^\/login(?:\/|$)/, "doc.title.login"],
  [/^\/register(?:\/|$)/, "doc.title.register"],
  [/^\/farm-select(?:\/|$)/, "doc.title.farmSelect"],
  [/^\/worker\/login(?:\/|$)/, "doc.title.workerLogin"],
  [/^\/worker\/offline(?:\/|$)/, "doc.title.workerOffline"],
  [/^\/worker(?:\/|$)/, "doc.title.worker"],
  [/^\/dashboard(?:\/|$)/, "doc.title.dashboard"],
  [/^\/owner(?:\/|$)/, "doc.title.owner"],
  [/^\/animals\/new(?:\/|$)/, "doc.title.addAnimal"],
  [/^\/animals\/\d+(?:\/|$)/, "doc.title.animal"],
  [/^\/animals(?:\/|$)/, "doc.title.animals"],
  [/^\/buckets(?:\/|$)/, "doc.title.buckets"],
  [/^\/breeding\/[^/]+\/ultrasound(?:\/|$)/, "doc.title.ultrasound"],
  [/^\/breeding(?:\/|$)/, "doc.title.breeding"],
  [/^\/kidding\/new(?:\/|$)/, "doc.title.recordBirth"],
  [/^\/kidding(?:\/|$)/, "doc.title.births"],
  [/^\/health\/new(?:\/|$)/, "doc.title.addHealthEvent"],
  [/^\/health\/schedule(?:\/|$)/, "doc.title.vaccinationSchedule"],
  [/^\/health(?:\/|$)/, "doc.title.health"],
  [/^\/screening(?:\/|$)/, "doc.title.screening"],
  [/^\/feeding\/inventory(?:\/|$)/, "doc.title.feedInventory"],
  [/^\/feeding\/recipes(?:\/|$)/, "doc.title.feedRecipes"],
  [/^\/feeding(?:\/|$)/, "doc.title.feeding"],
  [/^\/purchases(?:\/|$)/, "doc.title.purchases"],
  [/^\/tasks(?:\/|$)/, "doc.title.tasks"],
  [/^\/finance\/insurance(?:\/|$)/, "doc.title.insurance"],
  [/^\/finance(?:\/|$)/, "doc.title.finance"],
  [/^\/planner(?:\/|$)/, "doc.title.planner"],
  [/^\/simulation(?:\/|$)/, "doc.title.simulation"],
  [/^\/ops-simulation(?:\/|$)/, "doc.title.opsSimulation"],
  [/^\/reports(?:\/|$)/, "doc.title.reports"],
  [/^\/team(?:\/|$)/, "doc.title.team"],
  [/^\/no-access(?:\/|$)/, "doc.title.noAccess"],
];

export function routeTitleKey(pathname: string): MessageKey | null {
  return ROUTE_TITLES.find(([pattern]) => pattern.test(pathname))?.[1] ?? null;
}

export function useDocumentTitle(pathname: string) {
  const t = useT();
  useEffect(() => {
    const titleKey = routeTitleKey(pathname);
    document.title = `${t(titleKey ?? "doc.title.app")} · ${APP_NAME}`;
  }, [pathname, t]);
}

/** Global binding for public/auth/worker routes. The authenticated shell also
 * calls the same hook directly so isolated shell tests exercise the contract. */
export function DocumentTitleSync() {
  useDocumentTitle(usePathname());
  return null;
}

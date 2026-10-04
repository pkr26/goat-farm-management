import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { APP_NAME } from "@/lib/brand";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "@/lib/i18n";
import { DocumentTitleSync, routeTitleKey } from "@/components/document-title-sync";

const nav = vi.hoisted(() => ({ pathname: "/login" }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.pathname }));

afterEach(() => {
  localStorage.clear();
  document.documentElement.lang = "en";
  document.title = "";
});

describe("DocumentTitleSync", () => {
  it.each([
    ["/login", "doc.title.login"],
    ["/register", "doc.title.register"],
    ["/farm-select", "doc.title.farmSelect"],
    ["/worker/login", "doc.title.workerLogin"],
    ["/worker/offline", "doc.title.workerOffline"],
    ["/worker", "doc.title.worker"],
    ["/owner", "doc.title.owner"],
    ["/finance/insurance", "doc.title.insurance"],
  ] as const)("maps %s to %s", (pathname, key) => {
    expect(routeTitleKey(pathname)).toBe(key);
  });

  it("localizes a public page title from the persisted language", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    nav.pathname = "/login";
    render(
      <LanguageProvider>
        <DocumentTitleSync />
      </LanguageProvider>,
    );

    await waitFor(() => expect(document.title).toBe(`సైన్ ఇన్ · ${APP_NAME}`));
  });

  it("uses a localized generic title for an unknown path", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    nav.pathname = "/unknown";
    render(
      <LanguageProvider>
        <DocumentTitleSync />
      </LanguageProvider>,
    );

    await waitFor(() => expect(document.title).toBe(`పశుసంపద ఫారం నిర్వహణ · ${APP_NAME}`));
  });
});

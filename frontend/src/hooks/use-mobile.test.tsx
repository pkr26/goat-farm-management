import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useIsMobile } from "./use-mobile";

function Probe() {
  return <output aria-label="viewport mode">{useIsMobile() ? "mobile" : "desktop"}</output>;
}

describe("useIsMobile", () => {
  let matches = false;
  const listeners = new Set<() => void>();

  beforeEach(() => {
    matches = false;
    listeners.clear();
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 500 });
    vi.stubGlobal("matchMedia", vi.fn(
      () =>
        ({
          get matches() {
            return matches;
          },
          media: "(max-width: 767px)",
          onchange: null,
          addEventListener: (_type: string, listener: EventListenerOrEventListenerObject) => {
            listeners.add(listener as () => void);
          },
          removeEventListener: (_type: string, listener: EventListenerOrEventListenerObject) => {
            listeners.delete(listener as () => void);
          },
          addListener: vi.fn(),
          removeListener: vi.fn(),
          dispatchEvent: vi.fn(),
        }) as MediaQueryList,
    ));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("reads the same media query source that triggers its subscription", () => {
    render(<Probe />);

    // Deliberately disagree with innerWidth. The media query is the source
    // that can notify the store, so its snapshot must also be authoritative.
    expect(screen.getByLabelText("viewport mode")).toHaveTextContent("desktop");

    act(() => {
      matches = true;
      for (const listener of listeners) listener();
    });
    expect(screen.getByLabelText("viewport mode")).toHaveTextContent("mobile");
  });
});

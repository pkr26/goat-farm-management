import { renderToString } from "react-dom/server";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useIsMobile } from "./use-mobile";

function Probe() {
  return <output aria-label="viewport mode">{useIsMobile() ? "mobile" : "desktop"}</output>;
}

describe("useIsMobile", () => {
  let matches = false;
  const listeners = new Set<() => void>();
  const addEventListener = vi.fn(
    (type: string, listener: EventListenerOrEventListenerObject) => {
      if (type === "change") listeners.add(listener as () => void);
    },
  );
  const removeEventListener = vi.fn(
    (type: string, listener: EventListenerOrEventListenerObject) => {
      if (type === "change") listeners.delete(listener as () => void);
    },
  );

  beforeEach(() => {
    matches = false;
    listeners.clear();
    addEventListener.mockClear();
    removeEventListener.mockClear();
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 500 });
    vi.stubGlobal("matchMedia", vi.fn(
      () =>
        ({
          get matches() {
            return matches;
          },
          media: "(max-width: 767px)",
          onchange: null,
          addEventListener,
          removeEventListener,
          addListener: vi.fn(),
          removeListener: vi.fn(),
          dispatchEvent: vi.fn(),
        }) as MediaQueryList,
    ));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("reads the same media query source that triggers its subscription", () => {
    render(<Probe />);

    expect(window.matchMedia).toHaveBeenCalledWith("(max-width: 767px)");
    expect(addEventListener).toHaveBeenCalledWith("change", expect.any(Function));

    // Deliberately disagree with innerWidth. The media query is the source
    // that can notify the store, so its snapshot must also be authoritative.
    expect(screen.getByLabelText("viewport mode")).toHaveTextContent("desktop");

    act(() => {
      matches = true;
      for (const listener of listeners) listener();
    });
    expect(screen.getByLabelText("viewport mode")).toHaveTextContent("mobile");
  });

  it("removes its media-query listener when the consumer unmounts", () => {
    const view = render(<Probe />);
    expect(listeners.size).toBe(1);

    view.unmount();

    expect(listeners.size).toBe(0);
    expect(removeEventListener).toHaveBeenCalledWith(
      "change",
      expect.any(Function),
    );
  });

  it("uses the deterministic desktop snapshot during server rendering", () => {
    matches = true;

    expect(renderToString(<Probe />)).toContain("desktop");
  });
});

"use client";

import { useCallback, useRef, useState } from "react";

/**
 * Runs at most one copy of an async UI action at a time. The ref closes the
 * same-render double-click gap while `pending` keeps controls disabled until
 * the original request settles.
 */
export function useSingleFlight() {
  const active = useRef(false);
  const [pending, setPending] = useState(false);

  const run = useCallback(async <T>(action: () => Promise<T>): Promise<T | undefined> => {
    if (active.current) return undefined;
    active.current = true;
    setPending(true);
    try {
      return await action();
    } finally {
      active.current = false;
      setPending(false);
    }
  }, []);

  return { pending, run };
}

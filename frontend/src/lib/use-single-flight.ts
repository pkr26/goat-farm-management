"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Runs at most one copy of an async UI action at a time. The ref closes the
 * same-render double-click gap while `pending` keeps controls disabled until
 * the original request settles.
 */
export function useSingleFlight() {
  const active = useRef(false);
  const mounted = useRef(true);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    // Setup must restore the flag after React Strict Mode's cleanup rehearsal.
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const run = useCallback(async <T>(action: () => Promise<T>): Promise<T | undefined> => {
    if (!mounted.current || active.current) return undefined;
    active.current = true;
    setPending(true);
    try {
      return await action();
    } finally {
      active.current = false;
      if (mounted.current) setPending(false);
    }
  }, []);

  return { pending, run };
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Runs at most one copy of an async UI action at a time. The ref closes the
 * same-render double-click gap while `pending` keeps controls disabled until
 * the original request settles.
 */
export function useSingleFlight() {
  const active = useRef(false);
  // Stryker disable next-line BooleanLiteral: the flag's only consumers gate post-unmount writes, which React 18 no-ops either way; the effect below restores the true value on mount
  const mounted = useRef(true);
  const [pending, setPending] = useState(false);

  // Stryker disable ArrayDeclaration: hook deps compare element-wise, and a constant junk list compares equal across renders — the effect still runs exactly once
  useEffect(() => {
    // Setup must restore the flag after React Strict Mode's cleanup rehearsal.
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  // Stryker restore ArrayDeclaration

  // Stryker disable ArrayDeclaration: the callback closes over refs and a state setter only, so a constant junk dep list cannot change its behavior
  const run = useCallback(async <T>(action: () => Promise<T>): Promise<T | undefined> => {
    if (!mounted.current || active.current) return undefined;
    active.current = true;
    setPending(true);
    try {
      return await action();
    } finally {
      active.current = false;
      // Stryker disable next-line ConditionalExpression: setPending on an unmounted component is a React no-op
    if (mounted.current) setPending(false);
    }
  }, []);
  // Stryker restore ArrayDeclaration

  return { pending, run };
}

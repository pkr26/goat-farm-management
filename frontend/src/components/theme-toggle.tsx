"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useRef, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";

const emptySubscribe = () => () => {};

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const pendingTheme = useRef<"light" | "dark" | null>(null);
  // Render a neutral placeholder on the server / first client render so the
  // icon never mismatches the theme applied by next-themes.
  const mounted = useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );
  useEffect(() => {
    if (pendingTheme.current === resolvedTheme) pendingTheme.current = null;
  }, [resolvedTheme]);

  if (!mounted) {
    // Placeholder keeps layout stable until the theme is known client-side.
    return <Button variant="ghost" size="icon" aria-label="Toggle theme" />;
  }

  const isDark = resolvedTheme === "dark";

  function toggleTheme() {
    // next-themes publishes resolvedTheme on a later render. Compose rapid
    // clicks from the latest requested value so they do not all target the
    // same captured theme.
    const current = pendingTheme.current ?? (isDark ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    pendingTheme.current = next;
    setTheme(next);
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={toggleTheme}
    >
      {isDark ? <Sun /> : <Moon />}
    </Button>
  );
}

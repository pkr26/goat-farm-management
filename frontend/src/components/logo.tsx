import { cn } from "@/lib/utils";

import { APP_NAME } from "@/lib/brand";

export { APP_NAME };

/**
 * Brand mark: a horned livestock head — the wide crescent horns read as the
 * Murrah buffalo (the flagship dairy species) while staying a generic bovid
 * for goat herds. Same line-art language as the app icon set (1.8 stroke,
 * round caps) so it sits naturally at 16–24px.
 */
function HerdMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {/* wide crescent horns sweeping up and out */}
      <path d="M7.8 9.8C5.2 9.2 3.2 7 3 3.8" />
      <path d="M16.2 9.8C18.8 9.2 20.8 7 21 3.8" />
      {/* droopy ears under the horn bases */}
      <path d="M7.6 11.5c-1.6.2-2.7 1.1-3.2 2.4" />
      <path d="M16.4 11.5c1.6.2 2.7 1.1 3.2 2.4" />
      {/* broad head and muzzle */}
      <path d="M12 7.4c-2.9 0-5 2.2-5 5.1 0 3 1.9 5.4 3.4 6.5.5.4 1 .6 1.6.6s1.1-.2 1.6-.6c1.5-1.1 3.4-3.5 3.4-6.5 0-2.9-2.1-5.1-5-5.1Z" />
      {/* nostrils */}
      <path d="M10.3 14.4v.01" />
      <path d="M13.7 14.4v.01" />
    </svg>
  );
}

export function Logo({
  className,
  withWordmark = true,
}: {
  className?: string;
  withWordmark?: boolean;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <HerdMark className="size-5" />
      </span>
      {withWordmark && (
        <span className="font-heading text-lg font-semibold tracking-tight">
          {APP_NAME}
        </span>
      )}
    </span>
  );
}

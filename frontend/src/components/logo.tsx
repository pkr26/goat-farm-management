import { cn } from "@/lib/utils";

function GoatMark({ className }: { className?: string }) {
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
      {/* horns */}
      <path d="M8.6 8.6C7.3 6.7 6.9 4.5 7.7 2.7" />
      <path d="M15.4 8.6c1.3-1.9 1.7-4.1.9-5.9" />
      {/* ears */}
      <path d="M8 10.6 4.7 9.3" />
      <path d="m16 10.6 3.3-1.3" />
      {/* face */}
      <path d="M12 7.6c-2.2 0-3.8 1.7-3.8 4.1 0 2.6 1.5 4.7 2.7 5.7.4.4.8.6 1.1.6s.7-.2 1.1-.6c1.2-1 2.7-3.1 2.7-5.7 0-2.4-1.6-4.1-3.8-4.1Z" />
      {/* goatee */}
      <path d="M12 18v2.6" />
      {/* nostrils */}
      <path d="M11 14.1v.01" />
      <path d="M13 14.1v.01" />
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
        <GoatMark className="size-5" />
      </span>
      {withWordmark && (
        <span className="font-heading text-lg font-semibold tracking-tight">
          GoatFarm
        </span>
      )}
    </span>
  );
}

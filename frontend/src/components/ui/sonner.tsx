"use client"

import { useTheme } from "next-themes"
import { Toaster as Sonner, type ToasterProps } from "sonner"
import { CircleCheckIcon, InfoIcon, TriangleAlertIcon, OctagonXIcon, Loader2Icon } from "lucide-react"

const Toaster = ({ ...props }: ToasterProps) => {
  const { theme = "system" } = useTheme()

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      className="toaster group"
      icons={{
        success: (
          <CircleCheckIcon className="size-4" />
        ),
        info: (
          <InfoIcon className="size-4" />
        ),
        warning: (
          <TriangleAlertIcon className="size-4" />
        ),
        error: (
          <OctagonXIcon className="size-4" />
        ),
        loading: (
          <Loader2Icon className="size-4 animate-spin" />
        ),
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
        } as React.CSSProperties
      }
      toastOptions={{
        classNames: {
          // Status hues come from the app's semantic token ramp, never
          // sonner's richColors palette — the library colors match neither
          // the light nor the dark theme (2026-09-28 audit). Token utilities
          // resolve per theme, so no dark: variants are needed here.
          success: "border-success/40 bg-success-tint text-success-tint-foreground",
          info: "border-info/40 bg-info-tint text-info-tint-foreground",
          warning: "border-warning/40 bg-warning-tint text-warning-tint-foreground",
          error: "border-destructive/40 bg-destructive/10 text-destructive",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }

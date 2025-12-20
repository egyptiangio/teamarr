import * as React from "react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

interface TooltipRow {
  label: string
  value: string | number
  logo?: string
}

interface RichTooltipProps {
  children: React.ReactNode
  title?: string
  rows?: TooltipRow[]
  content?: React.ReactNode
  side?: "top" | "right" | "bottom" | "left"
  align?: "start" | "center" | "end"
  className?: string
  disabled?: boolean
}

export function RichTooltip({
  children,
  title,
  rows,
  content,
  side = "bottom",
  align = "center",
  className,
  disabled = false,
}: RichTooltipProps) {
  if (disabled || (!rows?.length && !content)) {
    return <>{children}</>
  }

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent
          side={side}
          align={align}
          className={cn(
            "bg-popover border shadow-lg p-0 min-w-[160px] max-w-[280px]",
            className
          )}
        >
          {title && (
            <div className="px-3 py-2 border-b text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {title}
            </div>
          )}
          {content ? (
            <div className="p-3">{content}</div>
          ) : rows && rows.length > 0 ? (
            <div className="py-1">
              {rows.map((row, index) => (
                <div
                  key={index}
                  className="flex items-center justify-between gap-3 px-3 py-1.5 text-sm"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    {row.logo && (
                      <img
                        src={row.logo}
                        alt=""
                        className="h-4 w-4 object-contain flex-shrink-0"
                        onError={(e) => {
                          e.currentTarget.style.display = "none"
                        }}
                      />
                    )}
                    <span className="text-muted-foreground truncate">
                      {row.label}
                    </span>
                  </div>
                  <span className="font-medium text-primary flex-shrink-0">
                    {row.value}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

// Stat tile component for Dashboard quadrants
interface StatTileProps {
  value: string | number
  label: string
  sublabel?: string
  tooltipTitle?: string
  tooltipRows?: TooltipRow[]
  tooltipContent?: React.ReactNode
  className?: string
  onClick?: () => void
}

export function StatTile({
  value,
  label,
  sublabel,
  tooltipTitle,
  tooltipRows,
  tooltipContent,
  className,
  onClick,
}: StatTileProps) {
  const hasTooltip = tooltipRows?.length || tooltipContent

  const tile = (
    <div
      className={cn(
        "bg-muted/50 rounded-md p-2 text-center transition-colors",
        hasTooltip && "cursor-help hover:bg-muted",
        onClick && "cursor-pointer hover:bg-muted",
        className
      )}
      onClick={onClick}
    >
      <div className="text-xl font-bold text-primary leading-none">
        {value}
      </div>
      <div className="text-[10px] text-muted-foreground mt-0.5 uppercase tracking-wide">
        {label}
        {sublabel && (
          <span className="text-foreground/60 ml-1">({sublabel})</span>
        )}
      </div>
    </div>
  )

  if (!hasTooltip) {
    return tile
  }

  return (
    <RichTooltip
      title={tooltipTitle}
      rows={tooltipRows}
      content={tooltipContent}
    >
      {tile}
    </RichTooltip>
  )
}

// Quadrant card for Dashboard
interface QuadrantProps {
  title: string
  manageLink?: string
  onManageClick?: () => void
  children: React.ReactNode
  className?: string
}

export function Quadrant({
  title,
  manageLink,
  onManageClick,
  children,
  className,
}: QuadrantProps) {
  return (
    <div
      className={cn(
        "bg-card border rounded-lg p-3",
        className
      )}
    >
      <div className="flex items-center justify-between mb-2 pb-1.5 border-b">
        <h3 className="font-semibold text-sm">{title}</h3>
        {(manageLink || onManageClick) && (
          <button
            onClick={onManageClick}
            className="text-xs text-muted-foreground hover:text-primary transition-colors"
          >
            Manage →
          </button>
        )}
      </div>
      <div className="grid grid-cols-4 gap-1.5">{children}</div>
    </div>
  )
}

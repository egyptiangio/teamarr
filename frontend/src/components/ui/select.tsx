import * as React from "react"
import { cn } from "@/lib/utils"
import { ChevronDown } from "lucide-react"

export interface SelectProps
  extends React.SelectHTMLAttributes<HTMLSelectElement> {}

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div className="relative">
        <select
          className={cn(
            // V1-matching: solid background, visible border, proper text color
            "flex h-9 w-full appearance-none rounded-md border border-input px-3 py-1 pr-8 text-sm shadow-sm transition-colors",
            "bg-secondary text-foreground cursor-pointer",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:border-primary",
            "disabled:cursor-not-allowed disabled:opacity-50",
            className
          )}
          ref={ref}
          {...props}
        >
          {children}
        </select>
        <ChevronDown className="absolute right-2 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" />
      </div>
    )
  }
)
Select.displayName = "Select"

export { Select }

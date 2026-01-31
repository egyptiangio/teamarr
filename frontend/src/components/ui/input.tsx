import * as React from "react"
import { cn } from "@/lib/utils"

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          // V1-matching: solid background, visible border, proper text color
          "flex h-9 w-full rounded-md border border-input px-3 py-1 text-sm shadow-sm transition-colors",
          // Background: use secondary (like V1's bg-tertiary)
          "bg-secondary text-foreground",
          // File inputs
          "file:border-0 file:bg-transparent file:text-sm file:font-medium",
          // Placeholder
          "placeholder:text-muted-foreground",
          // Focus state
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:border-primary",
          // Disabled state
          "disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input }

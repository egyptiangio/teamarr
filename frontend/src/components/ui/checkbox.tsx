import * as React from "react"
import { cn } from "@/lib/utils"
import { Check } from "lucide-react"

interface CheckboxProps {
  checked?: boolean
  disabled?: boolean
  onCheckedChange?: (checked: boolean) => void
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  className?: string
  id?: string
}

const Checkbox = React.forwardRef<HTMLButtonElement, CheckboxProps>(
  ({ className, checked, onCheckedChange, onClick, disabled, id }, ref) => {
    return (
      <button
        ref={ref}
        id={id}
        type="button"
        role="checkbox"
        aria-checked={checked}
        disabled={disabled}
        data-state={checked ? "checked" : "unchecked"}
        className={cn(
          "peer h-5 w-5 shrink-0 rounded border-2 shadow-sm cursor-pointer",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          "disabled:cursor-not-allowed disabled:opacity-50",
          "flex items-center justify-center transition-colors",
          // Unchecked: solid visible border
          !checked && "border-muted-foreground bg-transparent hover:border-primary hover:bg-primary/10",
          // Checked: filled background
          checked && "bg-primary border-primary text-primary-foreground",
          className
        )}
        onClick={(e) => {
          onClick?.(e)
          onCheckedChange?.(!checked)
        }}
      >
        {checked && (
          <Check className="h-3.5 w-3.5 text-current" strokeWidth={3} />
        )}
      </button>
    )
  }
)
Checkbox.displayName = "Checkbox"

export { Checkbox }

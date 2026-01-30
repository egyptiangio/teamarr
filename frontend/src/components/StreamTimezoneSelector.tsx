import { Select } from "@/components/ui/select"

// Timezone options with representative cities
// Format: "Abbreviation (City)" => IANA timezone
const TIMEZONE_OPTIONS: Array<{ label: string; value: string }> = [
  // North America
  { label: "ET (New York)", value: "America/New_York" },
  { label: "CT (Chicago)", value: "America/Chicago" },
  { label: "MT (Denver)", value: "America/Denver" },
  { label: "PT (Los Angeles)", value: "America/Los_Angeles" },
  { label: "AKT (Anchorage)", value: "America/Anchorage" },
  { label: "HST (Honolulu)", value: "Pacific/Honolulu" },
  { label: "AT (Halifax)", value: "America/Halifax" },
  { label: "NT (St. John's)", value: "America/St_Johns" },

  // Europe
  { label: "GMT/UTC (London)", value: "Europe/London" },
  { label: "CET (Paris)", value: "Europe/Paris" },
  { label: "EET (Helsinki)", value: "Europe/Helsinki" },

  // Australia
  { label: "AEST (Sydney)", value: "Australia/Sydney" },
  { label: "ACST (Adelaide)", value: "Australia/Adelaide" },
  { label: "AWST (Perth)", value: "Australia/Perth" },

  // Asia
  { label: "JST (Tokyo)", value: "Asia/Tokyo" },
  { label: "KST (Seoul)", value: "Asia/Seoul" },
  { label: "CST (Shanghai)", value: "Asia/Shanghai" },
  { label: "IST (Kolkata)", value: "Asia/Kolkata" },
  { label: "GST (Dubai)", value: "Asia/Dubai" },

  // Other
  { label: "NZST (Auckland)", value: "Pacific/Auckland" },
  { label: "BRT (Sao Paulo)", value: "America/Sao_Paulo" },
  { label: "SAST (Johannesburg)", value: "Africa/Johannesburg" },
]

interface StreamTimezoneSelectorProps {
  value: string | null
  onChange: (value: string | null) => void
  disabled?: boolean
}

export function StreamTimezoneSelector({
  value,
  onChange,
  disabled = false,
}: StreamTimezoneSelectorProps) {
  return (
    <Select
      value={value || ""}
      onChange={(e) => onChange(e.target.value || null)}
      disabled={disabled}
    >
      <option value="">Auto-detect from stream</option>
      {TIMEZONE_OPTIONS.map((tz) => (
        <option key={tz.value} value={tz.value}>
          {tz.label}
        </option>
      ))}
    </Select>
  )
}

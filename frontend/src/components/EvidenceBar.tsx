import type { ReactNode } from "react"

interface Props {
  type?: string
  name: string
  status?: ReactNode
  time?: string
  action?: ReactNode
  onClick?: () => void
  icon?: ReactNode
  className?: string
}

export default function EvidenceBar({
  type,
  name,
  status,
  time,
  action,
  onClick,
  icon,
  className = ""
}: Props) {
  const simpleClickable = Boolean(onClick && !action)

  return (
    <div
      onClick={onClick}
      role={simpleClickable ? "button" : undefined}
      tabIndex={simpleClickable ? 0 : undefined}
      onKeyDown={simpleClickable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick?.() } } : undefined}
      className={`flex items-center gap-3 px-3 py-2 rounded transition-colors group ${
        onClick ? "cursor-pointer hover:bg-line/10 focus-visible:outline-2 focus-visible:outline-primary/30" : ""
      } ${className}`}
    >
      {icon && <div className="flex-shrink-0">{icon}</div>}
      
      <div className="flex-1 min-w-0 flex items-center gap-2 text-xs">
        {type && (
          <>
            <span className="text-muted/60">{type}</span>
            <span className="text-line">/</span>
          </>
        )}
        <span className="text-ink font-medium truncate">{name}</span>
        {status && (
          <>
            <span className="text-line">/</span>
            {status}
          </>
        )}
        {time && (
          <>
            <span className="text-line">/</span>
            <span className="text-muted/60">{time}</span>
          </>
        )}
      </div>

      {action && (
        <div className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          {action}
        </div>
      )}
    </div>
  )
}

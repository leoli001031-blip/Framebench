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
  const content = (
    <>
      {icon && <div className="flex-shrink-0">{icon}</div>}

      <div className="flex-1 min-w-0 flex items-center gap-2 text-xs">
        {type && (
          <>
            <span className="text-muted">{type}</span>
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
            <span className="text-muted truncate">{time}</span>
          </>
        )}
      </div>
    </>
  )

  return (
    <div
      className={`flex items-center gap-3 px-3 py-2 rounded transition-colors group ${
        onClick ? "hover:bg-line/10 focus-within:bg-line/10" : ""
      } ${className}`}
    >
      {onClick ? (
        <button
          type="button"
          onClick={onClick}
          className="min-w-0 flex flex-1 items-center gap-3 rounded text-left focus-visible:outline-2 focus-visible:outline-primary/35"
        >
          {content}
        </button>
      ) : (
        <div className="min-w-0 flex flex-1 items-center gap-3">{content}</div>
      )}

      {action && (
        <div className="flex-shrink-0 opacity-60 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
          {action}
        </div>
      )}
    </div>
  )
}

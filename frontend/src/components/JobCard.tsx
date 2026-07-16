import type { ReactNode } from "react"
import StatusBean from "./StatusBean"
import type { StatusType } from "./StatusBean"

interface Props {
  title: string
  status: StatusType
  statusLabel?: string
  conclusion?: string
  primaryAction?: ReactNode
  children?: ReactNode
  className?: string
}

export default function JobCard({
  title,
  status,
  statusLabel,
  conclusion,
  primaryAction,
  children,
  className = ""
}: Props) {
  return (
    <div className={`bg-surface rounded-2xl p-6 border border-line/10 shadow-[0_4px_20px_-4px_rgba(47,39,34,0.05)] ${className}`}>
      <div className="flex items-start justify-between gap-6 mb-4">
        <div className="min-w-0">
          <div className="flex items-center gap-3 mb-2">
            <h3 className="text-xl font-serif font-medium text-ink truncate tracking-normal">{title}</h3>
            <StatusBean type={status} label={statusLabel} />
          </div>
          {conclusion && (
            <p className="text-sm text-muted leading-relaxed font-medium">{conclusion}</p>
          )}
        </div>
        {primaryAction && (
          <div className="flex-shrink-0 pt-1">
            {primaryAction}
          </div>
        )}
      </div>
      {children && (
        <div className="mt-6 pt-6 border-t border-line/5">
          {children}
        </div>
      )}
    </div>
  )
}


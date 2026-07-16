export type StatusType = "processing" | "continuable" | "confirm" | "completed" | "failed" | "retry" | "pending" | "saved"

interface Props {
  type: StatusType
  label?: string
}

const statusConfig: Record<StatusType, { bg: string; text: string; label: string }> = {
  processing: { bg: "bg-primary-soft", text: "text-primary", label: "分析中" },
  continuable: { bg: "bg-sage/15", text: "text-sage", label: "可继续" },
  confirm: { bg: "bg-clay/15", text: "text-clay", label: "需确认" },
  completed: { bg: "bg-sage/15", text: "text-sage", label: "已完成" },
  failed: { bg: "bg-clay/15", text: "text-clay", label: "有问题" },
  retry: { bg: "bg-clay/15", text: "text-clay", label: "可重试" },
  pending: { bg: "bg-line/20", text: "text-muted", label: "等待中" },
  saved: { bg: "bg-sage/15", text: "text-sage", label: "已保存" },
}

export default function StatusBean({ type, label }: Props) {
  const config = statusConfig[type] || statusConfig.pending
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium leading-none ${config.bg} ${config.text}`}>
      {label || config.label}
    </span>
  )
}

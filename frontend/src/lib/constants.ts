import type { StatusType } from "@/components/StatusBean"

export function mapJobStatus(status: string): StatusType {
  switch (status) {
    case "pending": return "pending"
    case "preprocessing":
    case "preprocessing_done":
    case "analyzing":
    case "cancelling": return "processing"
    case "completed": return "completed"
    case "partial_completed": return "continuable"
    case "failed": return "failed"
    default: return "pending"
  }
}

export const STATUS_LABELS: Record<string, string> = {
  pending: "等待中", preprocessing: "分析中", preprocessing_done: "分析中",
  analyzing: "分析中", cancelling: "取消中", completed: "已完成", partial_completed: "部分完成", failed: "有问题",
}
export const STATUS_COLORS: Record<string, string> = {
  pending: "text-muted", preprocessing: "text-primary", preprocessing_done: "text-primary",
  analyzing: "text-primary", cancelling: "text-primary", completed: "text-sage", partial_completed: "text-sage", failed: "text-clay",
}

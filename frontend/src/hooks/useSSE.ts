import { useEffect, useRef } from "react"
import { getSSEUrl } from "@/lib/api"

export function useSSE(jobId: string | null, handlers: {
  onStatus?: (data: Record<string, unknown>) => void
  onShotStart?: (data: Record<string, unknown>) => void
  onShotDone?: (data: Record<string, unknown>) => void
  onThinking?: (data: Record<string, unknown>) => void
  onComplete?: (data: Record<string, unknown>) => void
  onError?: (data: Record<string, unknown>) => void
  onDone?: (status?: string) => void
}) {
  const handlersRef = useRef(handlers)

  useEffect(() => {
    handlersRef.current = handlers
  }, [handlers])

  useEffect(() => {
    if (!jobId) return

    const url = getSSEUrl(jobId)
    const evtSource = new EventSource(url)

    evtSource.addEventListener("status", (e) => {
      try { handlersRef.current.onStatus?.(JSON.parse(e.data)) } catch { /* skip malformed events */ }
    })
    evtSource.addEventListener("shot_start", (e) => {
      try { handlersRef.current.onShotStart?.(JSON.parse(e.data)) } catch { /* skip malformed events */ }
    })
    evtSource.addEventListener("shot_done", (e) => {
      try { handlersRef.current.onShotDone?.(JSON.parse(e.data)) } catch { /* skip malformed events */ }
    })
    evtSource.addEventListener("thinking", (e) => {
      try { handlersRef.current.onThinking?.(JSON.parse(e.data)) } catch { /* skip malformed events */ }
    })
    evtSource.addEventListener("complete", (e) => {
      try { handlersRef.current.onComplete?.(JSON.parse(e.data)) } catch { /* skip malformed events */ }
    })
    evtSource.addEventListener("job_error", (e) => {
      try { handlersRef.current.onError?.(JSON.parse(e.data)) } catch { /* skip malformed events */ }
    })
    evtSource.addEventListener("shot_error", (e) => {
      try { handlersRef.current.onError?.(JSON.parse(e.data)) } catch { /* skip malformed events */ }
    })
    evtSource.addEventListener("error", (e) => {
      const messageEvent = e as MessageEvent
      if (messageEvent.data) {
        try { handlersRef.current.onError?.(JSON.parse(messageEvent.data)) } catch { /* skip malformed events */ }
      }
    })
    evtSource.addEventListener("done", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        handlersRef.current.onDone?.(data.status)
      } catch { /* skip malformed events */ }
      evtSource.close()
    })

    evtSource.onerror = () => {
      handlersRef.current.onStatus?.({ phase: "reconnecting", message: "连接恢复中..." })
    }

    return () => evtSource.close()
  }, [jobId])
}

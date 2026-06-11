import { getApiBase, withAuthQuery } from "@/lib/runtime"

const API_BASE = getApiBase()
const frameUrlCache = new Map<string, string | null>()

export function getFrameUrl(keyframePaths: string): string | null {
  const cached = frameUrlCache.get(keyframePaths)
  if (cached !== undefined) return cached

  try {
    const paths: string[] = JSON.parse(keyframePaths)
    if (!paths[0]) {
      frameUrlCache.set(keyframePaths, null)
      return null
    }
    const p = paths[0].replace(/^.*?data\/jobs\//, "")
    const result = withAuthQuery(`${API_BASE}/frames/${p}`)
    frameUrlCache.set(keyframePaths, result)
    return result
  } catch {
    frameUrlCache.set(keyframePaths, null)
    return null
  }
}

export function getShotVideoUrl(jobId: string, startSec: number, endSec: number): string {
  const baseUrl = withAuthQuery(`${API_BASE}/jobs/${encodeURIComponent(jobId)}/video`)
  const start = Math.max(0, startSec).toFixed(2)
  const end = Math.max(startSec, endSec).toFixed(2)
  return `${baseUrl}#t=${start},${end}`
}

/** Sort category entries with "未分类" always last */
export function categorySort<T>([a]: [string, T], [b]: [string, T]): number {
  if (a === "未分类") return 1
  if (b === "未分类") return -1
  return a.localeCompare(b)
}

/** Trigger a browser file download from a blob */
export function downloadFile(content: string, filename: string, type: string = "text/markdown") {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

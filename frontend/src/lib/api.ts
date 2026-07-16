import type { JobInfo, JobDetail, JobSummary, JobShotsPage, JobProgress, TokenUsageSummary, CategoryList, StoryboardResult, StoryboardHistoryItem, StoryboardDetail, StoryboardGenerationTask, SystemSettingResponse, DataDiagnostics, ArchiveOrphanResponse, StoryboardShot, ConnectivityResponse, ConnectivityTestPayload, ReferenceBoardItem, ReferenceBoardPage } from "@/types"
import { getApiBase, withAuthHeaders, withAuthQuery } from "@/lib/runtime"

const BASE = getApiBase()

async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: withAuthHeaders(init.headers),
  })
}

async function getErrorDetail(res: Response): Promise<string> {
  try {
    const ct = res.headers.get("content-type") || ""
    if (ct.includes("application/json")) {
      const detail = (await res.json()).detail
      if (typeof detail === "string") return detail
      if (Array.isArray(detail)) {
        return detail
          .map((item) => {
            if (!item || typeof item !== "object") return String(item)
            const path = Array.isArray(item.loc) ? item.loc.slice(1).join(".") : ""
            const message = typeof item.msg === "string" ? item.msg : JSON.stringify(item)
            return path ? `${path}: ${message}` : message
          })
          .join("; ")
      }
      if (detail && typeof detail === "object") return JSON.stringify(detail)
    }
  } catch { /* non-JSON body, ignore */ }
  return ""
}

export async function uploadVideo(file: File): Promise<{ job_id: string; filename: string; status: string }> {
  const formData = new FormData()
  formData.append("file", file)
  const res = await apiFetch("/upload", { method: "POST", body: formData })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Upload failed")
  return res.json()
}

export async function startJob(jobId: string): Promise<{ job_id: string; status: string }> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/start`, { method: "POST" })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Start failed")
  return res.json()
}

export async function cancelJob(jobId: string): Promise<{ job_id: string; status: string }> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Cancel failed")
  return res.json()
}

function toQuery(params: Record<string, string | number | boolean | undefined>): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ""
}

export async function listJobs(options: { limit?: number; offset?: number; includeDeleted?: boolean; onlyDeleted?: boolean } = {}): Promise<JobInfo[]> {
  const res = await apiFetch(`/jobs${toQuery({
    limit: options.limit,
    offset: options.offset,
    include_deleted: options.includeDeleted,
    only_deleted: options.onlyDeleted,
  })}`)
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "List jobs failed")
  return res.json()
}

export async function getJob(jobId: string): Promise<JobDetail> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}`)
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Get job failed")
  return res.json()
}

export async function getJobSummary(jobId: string): Promise<JobSummary> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/summary`)
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Get job summary failed")
  return res.json()
}

export async function getJobShots(jobId: string, options: { limit?: number; offset?: number } = {}): Promise<JobShotsPage> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/shots${toQuery(options)}`)
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Get job shots failed")
  return res.json()
}

export async function getJobTokenUsage(jobId: string): Promise<TokenUsageSummary> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/token-usage`)
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Get token usage failed")
  return res.json()
}

export async function listReferenceBoard(options: { jobId?: string; limit?: number; offset?: number } = {}): Promise<ReferenceBoardPage> {
  const res = await apiFetch(`/reference-board${toQuery({
    job_id: options.jobId,
    limit: options.limit,
    offset: options.offset,
  })}`)
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Reference board failed")
  return res.json()
}

export async function addReferenceBoardShot(shotId: number): Promise<ReferenceBoardItem> {
  const res = await apiFetch(`/reference-board/shots/${shotId}`, { method: "PUT" })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Add reference shot failed")
  return res.json()
}

export async function removeReferenceBoardShot(shotId: number): Promise<void> {
  const res = await apiFetch(`/reference-board/shots/${shotId}`, { method: "DELETE" })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Remove reference shot failed")
}

export async function getJobProgress(
  jobId: string,
  options: { includeShots?: boolean; shotLimit?: number; shotOffset?: number } = {},
): Promise<JobProgress> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/progress${toQuery({
    include_shots: options.includeShots,
    shot_limit: options.shotLimit,
    shot_offset: options.shotOffset,
  })}`)
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Get job progress failed")
  return res.json()
}

export async function updateJob(jobId: string, data: { category?: string; filename?: string }): Promise<JobInfo> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Update failed")
  return res.json()
}

export async function renameCategory(oldName: string, newName: string): Promise<{ updated: number }> {
  const res = await apiFetch("/categories/rename", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_name: oldName, new_name: newName }),
  })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Rename category failed")
  return res.json()
}

export async function deleteJob(jobId: string): Promise<void> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Delete job failed")
}

export async function restoreJob(jobId: string): Promise<JobInfo> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/restore`, { method: "POST" })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Restore job failed")
  return res.json()
}

export async function permanentlyDeleteJob(jobId: string): Promise<void> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/permanent`, { method: "DELETE" })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Permanent delete failed")
}

export async function listCategories(): Promise<CategoryList> {
  const res = await apiFetch("/categories")
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "List categories failed")
  return res.json()
}

export async function generateStoryboard(
  brief: string,
  referenceJobIds: string[],
  targetDurationSec?: number,
  options: { referenceShotIds?: number[]; generateImages?: boolean } = {},
): Promise<StoryboardResult> {
  const res = await apiFetch("/generate-storyboard", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      brief,
      reference_job_ids: referenceJobIds,
      reference_shot_ids: options.referenceShotIds || [],
      target_duration_sec: targetDurationSec,
      generate_images: options.generateImages ?? true,
    }),
  })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Generate failed")
  return res.json()
}

export interface StoryboardStreamCallbacks {
  onStarted?: (task: StoryboardGenerationTask) => void
  onProgress: (message: string) => void
  onComplete: (result: StoryboardResult) => void
  onError: (message: string) => void
}

export async function generateStoryboardStream(
  brief: string,
  referenceJobIds: string[],
  targetDurationSec: number | undefined,
  callbacks: StoryboardStreamCallbacks,
  signal?: AbortSignal,
  clientTaskId?: string,
  options: { referenceShotIds?: number[]; generateImages?: boolean } = {},
): Promise<void> {
  let res: Response
  try {
    res = await apiFetch("/generate-storyboard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        brief,
        reference_job_ids: referenceJobIds,
        reference_shot_ids: options.referenceShotIds || [],
        target_duration_sec: targetDurationSec,
        generate_images: options.generateImages ?? true,
        client_task_id: clientTaskId,
      }),
      signal,
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") return
    callbacks.onError(e instanceof Error ? e.message : "连接中断")
    return
  }

  if (!res.ok) {
    callbacks.onError((await getErrorDetail(res)) || "Generate failed")
    return
  }

  const reader = res.body?.getReader()
  if (!reader) {
    callbacks.onError("浏览器不支持流式响应")
    return
  }

  const decoder = new TextDecoder()
  let buffer = ""
  let settled = false

  const handleEventBlock = (block: string) => {
    let currentEvent = ""
    const dataLines: string[] = []

    for (const rawLine of block.split(/\r?\n/)) {
      const line = rawLine.trimEnd()
      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim()
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart())
      }
    }

    if (!currentEvent || dataLines.length === 0) return

    try {
      const data = JSON.parse(dataLines.join("\n"))
      if (currentEvent === "started") {
        callbacks.onStarted?.(data.task)
      } else if (currentEvent === "progress") {
        callbacks.onProgress(data.message)
      } else if (currentEvent === "complete") {
        settled = true
        callbacks.onComplete(data.result)
      } else if (currentEvent === "error") {
        settled = true
        callbacks.onError(data.message)
      }
    } catch {
      // Ignore malformed SSE chunks; the final close guard will surface missing results.
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      let boundary = buffer.indexOf("\n\n")
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        handleEventBlock(block)
        boundary = buffer.indexOf("\n\n")
      }
    }
    buffer += decoder.decode()
    if (buffer.trim()) handleEventBlock(buffer)
    if (!settled && !signal?.aborted) {
      callbacks.onError("分镜生成连接已结束，但没有收到完成结果；请稍后查看历史记录或重试。")
    }
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") return
    callbacks.onError(e instanceof Error ? e.message : "连接中断")
  } finally {
    reader.releaseLock()
  }
}

export async function getReport(jobId: string): Promise<string> {
  const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/report?format=md`)
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Get report failed")
  return res.text()
}

export function getSSEUrl(jobId: string): string {
  return withAuthQuery(`${BASE}/jobs/${encodeURIComponent(jobId)}/sse`)
}

export async function listStoryboards(options: { limit?: number; offset?: number } = {}): Promise<StoryboardHistoryItem[]> {
  const res = await apiFetch(`/storyboards${toQuery(options)}`)
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "List storyboards failed")
  return res.json()
}

export async function listStoryboardGenerations(): Promise<StoryboardGenerationTask[]> {
  const res = await apiFetch("/storyboard-generations")
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "List storyboard generations failed")
  return res.json()
}

export async function getStoryboard(id: string): Promise<StoryboardDetail> {
  const res = await apiFetch(`/storyboards/${id}`)
  if (!res.ok) throw new Error("Storyboard not found")
  return res.json()
}

export async function retryStoryboardShotImage(storyboardId: string, shotNumber: number): Promise<StoryboardShot> {
  const res = await apiFetch(`/storyboards/${storyboardId}/shots/${shotNumber}/image`, { method: "POST" })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Retry image failed")
  return res.json()
}

export async function deleteStoryboard(id: string): Promise<void> {
  const res = await apiFetch(`/storyboards/${id}`, { method: "DELETE" })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Delete storyboard failed")
}

export async function listSettings(): Promise<SystemSettingResponse[]> {
  const res = await apiFetch("/settings")
  if (!res.ok) throw new Error("List settings failed")
  return res.json()
}

export async function updateSetting(key: string, value: string): Promise<SystemSettingResponse> {
  const res = await apiFetch(`/settings/${encodeURIComponent(key)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Update setting failed")
  return res.json()
}

export async function testConnectivity(payload: ConnectivityTestPayload): Promise<ConnectivityResponse> {
  const res = await apiFetch("/settings/test-connectivity", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Connectivity test failed")
  return res.json()
}

export async function testImageConnectivity(payload: ConnectivityTestPayload): Promise<ConnectivityResponse> {
  const res = await apiFetch("/settings/test-image-connectivity", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Image connectivity test failed")
  return res.json()
}

export async function getDataDiagnostics(): Promise<DataDiagnostics> {
  const res = await apiFetch("/data-diagnostics")
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Data diagnostics failed")
  return res.json()
}

export async function archiveOrphanJobDir(id: string): Promise<ArchiveOrphanResponse> {
  const res = await apiFetch(`/data-diagnostics/orphans/${encodeURIComponent(id)}/archive`, { method: "POST" })
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Archive orphan job dir failed")
  return res.json()
}

export async function getBackendLog(): Promise<string> {
  const res = await apiFetch("/diagnostics/logs/backend")
  if (!res.ok) throw new Error((await getErrorDetail(res)) || "Get backend log failed")
  return res.text()
}

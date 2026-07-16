import { useState, useEffect, useMemo, useRef } from "react"
import { useParams, useNavigate } from "react-router-dom"
import { cancelJob, getJobProgress, startJob } from "@/lib/api"
import { mapJobStatus } from "@/lib/constants"
import { useSSE } from "@/hooks/useSSE"
import type { JobProgress } from "@/types"
import ShotCard from "@/components/ShotCard"
import JobCard from "@/components/JobCard"

const SHOT_PAGE_SIZE = 120

export default function ProgressPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const [job, setJob] = useState<JobProgress | null>(null)
  const [preprocessMsg, setPreprocessMsg] = useState("")
  const [preprocessStep, setPreprocessStep] = useState("")
  const [thinking, setThinking] = useState("")
  const [completedShots, setCompletedShots] = useState<Set<number>>(new Set())
  const [failedShots, setFailedShots] = useState<Set<number>>(new Set())
  const [error, setError] = useState("")
  const [elapsedSec, setElapsedSec] = useState(0)
  const [loadingMoreShots, setLoadingMoreShots] = useState(false)
  const [cancelSubmitting, setCancelSubmitting] = useState(false)
  const startedRef = useRef(false)
  const runningSinceRef = useRef<number | null>(null)
  const pendingThinkingRef = useRef("")
  const thinkingFrameRef = useRef<number | null>(null)

  const cancelPendingThinking = () => {
    if (thinkingFrameRef.current != null) {
      window.cancelAnimationFrame(thinkingFrameRef.current)
      thinkingFrameRef.current = null
    }
    pendingThinkingRef.current = ""
  }

  const queueThinkingUpdate = (text: string) => {
    pendingThinkingRef.current = text
    if (thinkingFrameRef.current != null) return
    thinkingFrameRef.current = window.requestAnimationFrame(() => {
      thinkingFrameRef.current = null
      setThinking(pendingThinkingRef.current)
    })
  }

  useEffect(() => () => {
    if (thinkingFrameRef.current != null) {
      window.cancelAnimationFrame(thinkingFrameRef.current)
      thinkingFrameRef.current = null
    }
    pendingThinkingRef.current = ""
  }, [])

  useEffect(() => {
    if (!jobId) return
    startedRef.current = false
    runningSinceRef.current = null
    getJobProgress(jobId, { includeShots: true, shotLimit: SHOT_PAGE_SIZE })
      .then((nextJob) => {
        setCompletedShots(new Set())
        setFailedShots(new Set())
        setElapsedSec(0)
        setLoadingMoreShots(false)
        cancelPendingThinking()
        setJob(nextJob)
      })
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
  }, [jobId])

  useEffect(() => {
    if (job?.status === "pending") {
      if (!jobId || startedRef.current) return
      startedRef.current = true
      startJob(jobId)
        .then((started) => {
          setJob((prev) => prev ? { ...prev, status: started.status } : prev)
        })
        .catch((e) => {
          startedRef.current = false
          setError(e instanceof Error ? e.message : "启动失败")
        })
    }
  }, [job, jobId])

  useEffect(() => {
    const running = job?.status === "preprocessing" || job?.status === "preprocessing_done" || job?.status === "analyzing" || job?.status === "cancelling"
    if (!running) return

    if (runningSinceRef.current == null) {
      runningSinceRef.current = Date.now()
    }

    const tick = () => {
      if (runningSinceRef.current != null) {
        setElapsedSec(Math.floor((Date.now() - runningSinceRef.current) / 1000))
      }
    }
    tick()
    const timer = window.setInterval(tick, 1000)
    return () => window.clearInterval(timer)
  }, [job?.status])

  const retryJob = async () => {
    if (!jobId) return
    setError("")
    setPreprocessMsg("")
    setPreprocessStep("")
    cancelPendingThinking()
    setThinking("")
    setCompletedShots(new Set())
    setFailedShots(new Set())
    setElapsedSec(0)
    runningSinceRef.current = Date.now()
    startedRef.current = true
    setJob((prev) => prev ? { ...prev, status: "preprocessing", error_message: null, progress: 0 } : prev)
    try {
      await startJob(jobId)
    } catch (e) {
      setError(e instanceof Error ? e.message : "启动失败")
    }
  }

  const stopJob = async () => {
    if (!jobId || cancelSubmitting) return
    setCancelSubmitting(true)
    setError("")
    try {
      const result = await cancelJob(jobId)
      setJob((prev) => prev ? {
        ...prev,
        status: result.status,
        error_message: result.status === "failed" ? "Cancelled by user" : prev.error_message,
      } : prev)
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消失败")
      getJobProgress(jobId, { includeShots: true, shotLimit: SHOT_PAGE_SIZE })
        .then(setJob)
        .catch(() => undefined)
    } finally {
      setCancelSubmitting(false)
    }
  }

  useSSE(
    job?.status === "preprocessing" || job?.status === "preprocessing_done" || job?.status === "analyzing" || job?.status === "cancelling"
      ? (jobId ?? null)
      : null,
    {
      onStatus: (data) => {
        setPreprocessMsg(data.message as string)
        setPreprocessStep(data.step as string)
        if (data.message === "Analyzing..." || data.phase === "analyzing") {
          setJob((prev) => prev ? { ...prev, status: "analyzing" } : null)
        } else if (data.phase === "cancelling") {
          setJob((prev) => prev ? { ...prev, status: "cancelling" } : null)
        }
      },
      onShotStart: () => {},
      onShotDone: (data) => {
        const sn = data.shot_number as number
        if (typeof sn !== "number" || !Number.isFinite(sn) || sn < 1) return
        setCompletedShots((prev) => new Set(prev).add(sn))
      },
      onThinking: (data) => { queueThinkingUpdate(data.text as string) },
      onComplete: () => {
        if (!jobId) return
        getJobProgress(jobId, { includeShots: true, shotLimit: Math.max(SHOT_PAGE_SIZE, job?.shots.length || 0) })
          .then(setJob)
          .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
      },
      onOverviewFailed: (data) => {
        setJob((prev) => prev ? { ...prev, error_message: data.error as string } : null)
      },
      onError: (data) => {
        const sn = data.shot_number as number
        if (typeof sn === "number" && Number.isFinite(sn) && sn > 0) {
          setFailedShots((prev) => new Set(prev).add(sn))
          return
        }
        setJob((prev) => prev ? { ...prev, status: "failed", error_message: data.error as string } : null)
      },
      onDone: () => {
        if (!jobId) return
        getJobProgress(jobId, { includeShots: true, shotLimit: Math.max(SHOT_PAGE_SIZE, job?.shots.length || 0) })
          .then(setJob)
          .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
      },
    },
  )

  const savedCompletedShots = useMemo(
    () => new Set(job?.shots.filter((shot) => shot.status === "completed").map((shot) => shot.shot_number) || []),
    [job?.shots],
  )
  const savedFailedShots = useMemo(
    () => new Set(job?.shots.filter((shot) => shot.status === "failed").map((shot) => shot.shot_number) || []),
    [job?.shots],
  )
  const totalShots = job?.shots_total || job?.total_shots || job?.shots.length || 0
  const completedCount = Math.min(
    totalShots || Number.MAX_SAFE_INTEGER,
    Math.max(job?.completed_shots || 0, savedCompletedShots.size) + completedShots.size,
  )
  const failedCount = Math.min(
    totalShots || Number.MAX_SAFE_INTEGER,
    Math.max(job?.failed_shots || 0, savedFailedShots.size) + failedShots.size,
  )
  const progressPct = totalShots > 0 ? Math.min(100, Math.round(((completedCount + failedCount) / totalShots) * 100)) : Math.round((job?.progress || 0) * 100)
  const runningLabel = job?.status === "preprocessing" || job?.status === "preprocessing_done" ? "准备素材" : job?.status === "analyzing" ? "逐镜分析" : job?.status === "cancelling" ? "取消中" : job?.status === "partial_completed" ? "部分完成" : job?.status === "completed" ? "完成" : job?.status === "failed" ? "已中断" : "等待"

  const loadMoreShots = async () => {
    if (!jobId || !job || loadingMoreShots) return
    setLoadingMoreShots(true)
    try {
      const nextPage = await getJobProgress(jobId, {
        includeShots: true,
        shotLimit: SHOT_PAGE_SIZE,
        shotOffset: job.shots.length,
      })
      setJob((prev) => {
        if (!prev) return nextPage
        const seen = new Set(prev.shots.map((shot) => shot.id))
        return {
          ...nextPage,
          shots: [
            ...prev.shots,
            ...nextPage.shots.filter((shot) => !seen.has(shot.id)),
          ],
        }
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败")
    } finally {
      setLoadingMoreShots(false)
    }
  }

  const getConclusion = () => {
    if (job?.status === "failed" && /cancel|取消/i.test(job.error_message || "")) return "分析已取消，现有素材已安全保留。"
    if (error || job?.error_message) return `分析遇到问题: ${error || job?.error_message}`
    if (job?.status === "partial_completed") return "部分镜头已完成，报告可先查看；未完成镜头可稍后重试。"
    if (job?.status === "completed") return "分析已完成，报告已生成。"
    if (job?.status === "analyzing") return `正在深入分析镜头内容... (${completedCount}/${totalShots || "?"})`
    if (job?.status === "preprocessing") return `正在准备视频资源: ${preprocessMsg || "加载中"}`
    if (job?.status === "cancelling") return "正在安全停止当前分析任务..."
    return "正在排队，请稍候。"
  }

  return (
    <div className="max-w-2xl mx-auto">
      <JobCard
        title={job?.filename || "加载中..."}
        status={mapJobStatus(job?.status || "pending")}
        conclusion={getConclusion()}
        primaryAction={
          <div className="flex items-center gap-2">
            {(job?.status === "preprocessing" || job?.status === "preprocessing_done" || job?.status === "analyzing" || job?.status === "cancelling") && (
              <button
                onClick={stopJob}
                disabled={cancelSubmitting || job?.status === "cancelling"}
                className="px-4 py-2 rounded-lg border border-clay/25 text-clay text-sm hover:bg-clay/5 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {cancelSubmitting || job?.status === "cancelling" ? "取消中" : "取消分析"}
              </button>
            )}
            {(job?.status === "failed" || job?.status === "partial_completed") && (
              <button
                onClick={retryJob}
                className="px-4 py-2 rounded-lg bg-primary text-white text-sm hover:bg-primary/90 transition-all shadow-sm"
              >
                重试分析
              </button>
            )}
            {(job?.status === "completed" || job?.status === "partial_completed") && (
              <button
                onClick={() => navigate(`/jobs/${jobId}/report`)}
                className="px-4 py-2 rounded-lg bg-primary text-white text-sm hover:bg-primary/90 transition-all shadow-sm"
              >
                查看报告
              </button>
            )}
          </div>
        }
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs text-muted font-bold tracking-normal uppercase">
          <ProgressMetric label="当前阶段" value={runningLabel} />
          <ProgressMetric label="已完成" value={totalShots ? `${completedCount}/${totalShots}` : `${progressPct}%`} />
          <ProgressMetric label="失败镜头" value={`${failedCount}`} tone={failedCount > 0 ? "text-clay" : undefined} />
          <ProgressMetric label="已用时" value={formatElapsed(elapsedSec)} />
        </div>

        <div className="mt-5 h-1.5 rounded-full bg-line/20 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${failedCount > 0 ? "bg-clay/70" : "bg-primary"}`}
            style={{ width: `${Math.max(4, progressPct)}%` }}
          />
        </div>

        {/* Preprocessing Stepper */}
        {(job?.status === "preprocessing" || job?.status === "preprocessing_done") && (
          <div className="mt-2 flex items-center gap-4 text-xs text-muted">
            <ProgressDot label="检测" done={preprocessStep !== "shot_detection" && preprocessStep !== ""} active={preprocessStep === "shot_detection"} />
            <ProgressDot label="音频" done={["frame_extraction", "transcription"].includes(preprocessStep) || job?.status === "preprocessing_done"} active={preprocessStep === "audio_analysis"} />
            <ProgressDot label="抽帧" done={preprocessStep === "transcription" || job?.status === "preprocessing_done"} active={preprocessStep === "frame_extraction"} />
            <ProgressDot label="转录" done={job?.status === "preprocessing_done"} active={preprocessStep === "transcription"} />
          </div>
        )}

        {/* Analyzing Log */}
        {job?.status === "analyzing" && thinking && (
          <div className="mt-2 text-xs text-muted italic font-serif truncate">
            {thinking}
          </div>
        )}
      </JobCard>

      {/* Shot Evidence Bars */}
      {job?.shots && job.shots.length > 0 && (
        <div className="mt-8 space-y-4">
          <div className="flex items-center justify-between px-1">
            <h3 className="text-xs font-medium text-muted tracking-normal uppercase">分析详情</h3>
            <span className="text-xs text-muted">{job.shots.length} / {totalShots || job.shots.length}</span>
          </div>
          <div className="space-y-1">
            {job.shots.map((shot) => (
              <div key={shot.id} className="perf-row-sm">
                <ShotCard shot={shot} isComplete={completedShots.has(shot.shot_number) || savedCompletedShots.has(shot.shot_number)} />
              </div>
            ))}
          </div>
          {job.shots_truncated && (
            <div className="pt-3 text-center">
              <button
                onClick={loadMoreShots}
                disabled={loadingMoreShots}
                className="px-3 py-1.5 text-xs font-bold text-primary/60 hover:text-primary disabled:text-muted transition-colors uppercase tracking-normal"
              >
                {loadingMoreShots ? "正在加载" : "加载更多镜头"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ProgressMetric({ label, value, tone = "text-ink" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-muted mb-1">{label}</div>
      <div className={`text-sm normal-case tracking-normal truncate ${tone}`}>{value}</div>
    </div>
  )
}

function ProgressDot({ label, active, done }: { label: string; active: boolean; done: boolean }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-1.5 h-1.5 rounded-full ${done ? "bg-sage" : active ? "bg-primary animate-pulse" : "bg-line"}`} />
      <span className={done ? "text-muted" : active ? "text-ink" : "text-muted"}>{label}</span>
    </div>
  )
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  const rest = sec % 60
  return `${min}m ${rest}s`
}

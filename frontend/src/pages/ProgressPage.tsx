import { useState, useEffect, useRef } from "react"
import { useParams, useNavigate } from "react-router-dom"
import { getJob, startJob } from "@/lib/api"
import { mapJobStatus } from "@/lib/constants"
import { useSSE } from "@/hooks/useSSE"
import type { JobDetail } from "@/types"
import ShotCard from "@/components/ShotCard"
import JobCard from "@/components/JobCard"

export default function ProgressPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const [job, setJob] = useState<JobDetail | null>(null)
  const [preprocessMsg, setPreprocessMsg] = useState("")
  const [preprocessStep, setPreprocessStep] = useState("")
  const [thinking, setThinking] = useState("")
  const [completedShots, setCompletedShots] = useState<Set<number>>(new Set())
  const [failedShots, setFailedShots] = useState<Set<number>>(new Set())
  const [error, setError] = useState("")
  const [elapsedSec, setElapsedSec] = useState(0)
  const startedRef = useRef(false)
  const runningSinceRef = useRef<number | null>(null)

  useEffect(() => {
    if (!jobId) return
    startedRef.current = false
    runningSinceRef.current = null
    getJob(jobId)
      .then((nextJob) => {
        setCompletedShots(new Set())
        setFailedShots(new Set())
        setElapsedSec(0)
        setJob(nextJob)
      })
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
  }, [jobId])

  useEffect(() => {
    if (job && (job.status === "pending" || job.status === "preprocessing_done")) {
      if (!jobId || startedRef.current) return
      startedRef.current = true
      startJob(jobId).catch((e) => setError(e instanceof Error ? e.message : "启动失败"))
    }
  }, [job, jobId])

  useEffect(() => {
    const running = job?.status === "preprocessing" || job?.status === "preprocessing_done" || job?.status === "analyzing"
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

  useSSE(job?.status === "preprocessing" || job?.status === "analyzing" ? (jobId ?? null) : null, {
    onStatus: (data) => {
      setPreprocessMsg(data.message as string)
      setPreprocessStep(data.step as string)
      if (data.message === "Analyzing..." || data.phase === "analyzing") {
        setJob((prev) => prev ? { ...prev, status: "analyzing" } : null)
      }
    },
    onShotStart: () => {},
    onShotDone: (data) => {
      const sn = data.shot_number as number
      if (typeof sn !== "number" || !Number.isFinite(sn) || sn < 1) return
      setCompletedShots((prev) => new Set(prev).add(sn))
    },
    onThinking: (data) => { setThinking(data.text as string) },
    onComplete: () => { setJob((prev) => prev ? { ...prev, status: "completed" } : null) },
    onError: (data) => {
      const sn = data.shot_number as number
      if (typeof sn === "number" && Number.isFinite(sn) && sn > 0) {
        setFailedShots((prev) => new Set(prev).add(sn))
        return
      }
      setJob((prev) => prev ? { ...prev, status: "failed", error_message: data.error as string } : null)
    },
    onDone: () => { if (jobId) getJob(jobId).then(setJob).catch((e) => setError(e instanceof Error ? e.message : "加载失败")) },
  })

  const savedCompletedShots = new Set(job?.shots.filter((shot) => shot.status === "completed").map((shot) => shot.shot_number) || [])
  const savedFailedShots = new Set(job?.shots.filter((shot) => shot.status === "failed").map((shot) => shot.shot_number) || [])
  const totalShots = job?.total_shots || job?.shots.length || 0
  const completedCount = Math.max(completedShots.size, savedCompletedShots.size)
  const failedCount = Math.max(failedShots.size, savedFailedShots.size)
  const progressPct = totalShots > 0 ? Math.min(100, Math.round(((completedCount + failedCount) / totalShots) * 100)) : Math.round((job?.progress || 0) * 100)
  const runningLabel = job?.status === "preprocessing" || job?.status === "preprocessing_done" ? "准备素材" : job?.status === "analyzing" ? "逐镜分析" : job?.status === "cancelling" ? "取消中" : job?.status === "partial_completed" ? "部分完成" : job?.status === "completed" ? "完成" : job?.status === "failed" ? "已中断" : "等待"

  const getConclusion = () => {
    if (error || job?.error_message) return `分析遇到问题: ${error || job?.error_message}`
    if (job?.status === "partial_completed") return "部分镜头已完成，报告可先查看；未完成镜头可稍后重试。"
    if (job?.status === "completed") return "分析已完成，报告已生成。"
    if (job?.status === "analyzing") return `正在深入分析镜头内容... (${completedShots.size}/${job.total_shots || "?"})`
    if (job?.status === "preprocessing") return `正在准备视频资源: ${preprocessMsg || "加载中"}`
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
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-[10px] text-muted font-bold tracking-wider uppercase">
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
          <div className="mt-2 flex items-center gap-4 text-[10px] text-muted/60">
            <ProgressDot label="检测" done={preprocessStep !== "shot_detection" && preprocessStep !== ""} active={preprocessStep === "shot_detection"} />
            <ProgressDot label="音频" done={["frame_extraction", "transcription"].includes(preprocessStep) || job?.status === "preprocessing_done"} active={preprocessStep === "audio_analysis"} />
            <ProgressDot label="抽帧" done={preprocessStep === "transcription" || job?.status === "preprocessing_done"} active={preprocessStep === "frame_extraction"} />
            <ProgressDot label="转录" done={job?.status === "preprocessing_done"} active={preprocessStep === "transcription"} />
          </div>
        )}

        {/* Analyzing Log */}
        {job?.status === "analyzing" && thinking && (
          <div className="mt-2 text-[10px] text-muted/50 italic font-serif truncate">
            {thinking}
          </div>
        )}
      </JobCard>

      {/* Shot Evidence Bars */}
      {job?.shots && job.shots.length > 0 && (
        <div className="mt-8 space-y-4">
          <div className="flex items-center justify-between px-1">
            <h3 className="text-xs font-medium text-muted/80 tracking-wider uppercase">分析详情</h3>
            <span className="text-[10px] text-muted/40">{completedCount} / {totalShots || job.shots.length}</span>
          </div>
          <div className="space-y-1">
            {job.shots.map((shot) => (
              <ShotCard key={shot.id} shot={shot} isComplete={completedShots.has(shot.shot_number) || savedCompletedShots.has(shot.shot_number)} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ProgressMetric({ label, value, tone = "text-ink" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-muted/30 mb-1">{label}</div>
      <div className={`text-sm normal-case tracking-normal truncate ${tone}`}>{value}</div>
    </div>
  )
}

function ProgressDot({ label, active, done }: { label: string; active: boolean; done: boolean }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-1.5 h-1.5 rounded-full ${done ? "bg-sage" : active ? "bg-primary animate-pulse" : "bg-line"}`} />
      <span className={done ? "text-muted" : active ? "text-ink" : "text-muted/40"}>{label}</span>
    </div>
  )
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  const rest = sec % 60
  return `${min}m ${rest}s`
}

import { useRef, useState, useEffect, useMemo } from "react"
import { useParams, Link } from "react-router-dom"
import { getJobShots, getJobSummary, getReport } from "@/lib/api"
import { getFrameUrl, getShotVideoUrl, downloadFile } from "@/lib/utils"
import type { JobSummary, ShotInfo } from "@/types"
import JobCard from "@/components/JobCard"

const SHOT_PAGE_SIZE = 80

export default function ReportPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const [job, setJob] = useState<JobSummary | null>(null)
  const [shots, setShots] = useState<ShotInfo[]>([])
  const [shotsTotal, setShotsTotal] = useState(0)
  const [shotsTruncated, setShotsTruncated] = useState(false)
  const [reportMd, setReportMd] = useState("")
  const [reportError, setReportError] = useState("")
  const [reportLoading, setReportLoading] = useState(false)
  const [loadingMoreShots, setLoadingMoreShots] = useState(false)
  const [loading, setLoading] = useState(true)
  const [overviewOpen, setOverviewOpen] = useState(true)
  const [expandedShots, setExpandedShots] = useState<Set<number>>(new Set())

  useEffect(() => {
    if (!jobId) return
    let ignore = false
    void Promise.resolve().then(() => {
      if (ignore) return
      setLoading(true)
      setReportLoading(false)
      setReportMd("")
      setReportError("")
      setShots([])
      setShotsTotal(0)
      setShotsTruncated(false)
      Promise.all([
        getJobSummary(jobId),
        getJobShots(jobId, { limit: SHOT_PAGE_SIZE }),
      ])
        .then(([summary, shotPage]) => {
          if (ignore) return
          setJob(summary)
          setShots(shotPage.shots)
          setShotsTotal(shotPage.shots_total)
          setShotsTruncated(shotPage.shots_truncated)
          setLoading(false)
        })
        .catch(() => {
          if (ignore) return
          setJob(null)
          setLoading(false)
        })
    })
    return () => { ignore = true }
  }, [jobId])

  const shotStats = useMemo(() => {
    const totalDuration = job?.duration_sec ?? shots.reduce((sum, s) => sum + (s.end_time_sec - s.start_time_sec), 0)
    const analyzed = shots.filter((s) => s.analysis_text).length
    return {
      totalDuration,
      analyzed,
      complete: job?.status === "completed",
      allExpanded: shots.length > 0 && expandedShots.size === shots.length,
    }
  }, [expandedShots.size, job?.duration_sec, job?.status, shots])

  const handleExportReport = async () => {
    if (!jobId || !job) return
    setReportError("")
    let content = reportMd
    if (!content) {
      setReportLoading(true)
      try {
        content = await getReport(jobId)
        setReportMd(content)
      } catch (e) {
        setReportError(e instanceof Error ? e.message : "报告加载失败")
        return
      } finally {
        setReportLoading(false)
      }
    }
    downloadFile(content, `${job.filename}_拉片报告.md`)
  }

  const loadMoreShots = async () => {
    if (!jobId || loadingMoreShots) return
    setLoadingMoreShots(true)
    try {
      const page = await getJobShots(jobId, { limit: SHOT_PAGE_SIZE, offset: shots.length })
      setShots((prev) => {
        const seen = new Set(prev.map((shot) => shot.id))
        return [...prev, ...page.shots.filter((shot) => !seen.has(shot.id))]
      })
      setShotsTotal(page.shots_total)
      setShotsTruncated(page.shots_truncated)
    } catch (e) {
      setReportError(e instanceof Error ? e.message : "镜头加载失败")
    } finally {
      setLoadingMoreShots(false)
    }
  }

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto py-12 px-6">
        <div className="animate-pulse space-y-6">
          <div className="h-24 bg-surface rounded-xl" />
          <div className="h-64 bg-surface rounded-xl" />
        </div>
      </div>
    )
  }

  if (!job) return <div className="p-12 text-center text-muted">报告未找到</div>

  const { totalDuration, analyzed, complete, allExpanded } = shotStats

  const toggleShot = (shotNumber: number) => {
    setExpandedShots((prev) => {
      const next = new Set(prev)
      if (next.has(shotNumber)) next.delete(shotNumber)
      else next.add(shotNumber)
      return next
    })
  }

  const toggleAllShots = () => {
    setExpandedShots(allExpanded ? new Set() : new Set(shots.map((shot) => shot.shot_number)))
  }

  return (
    <div className="max-w-6xl mx-auto py-8 px-6">
      <Link to={`/jobs/${jobId}`} className="text-xs text-muted/60 hover:text-ink transition-colors inline-flex items-center gap-1 mb-6">
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>
        返回进度
      </Link>

      {/* Header JobCard */}
      <JobCard
        title={job.filename}
        status={complete ? "completed" : "processing"}
        statusLabel={complete ? "已分析" : `分析中 ${analyzed}/${shotsTotal || shots.length}`}
        conclusion="分析已整理完毕，可查看各镜头维度详情。"
        primaryAction={
          <div className="flex items-center gap-2">
            <button
              onClick={handleExportReport}
              disabled={reportLoading}
              className="px-4 py-2 bg-primary text-white text-sm rounded-lg hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm flex items-center gap-2"
            >
              {reportLoading ? "准备导出..." : "导出报告"}
            </button>
            {reportError && (
              <span className="text-[10px] text-clay">报告生成可能不完整</span>
            )}
          </div>
        }
      >
        <div className="flex items-center gap-6 text-[10px] text-muted font-medium tracking-tight uppercase">
          <div className="flex flex-col">
            <span className="text-muted/40 mb-0.5">镜头总数</span>
            <span className="text-ink">{shotsTotal || job.total_shots || shots.length} 镜</span>
          </div>
          <div className="w-px h-6 bg-line/20" />
          <div className="flex flex-col">
            <span className="text-muted/40 mb-0.5">总时长</span>
            <span className="text-ink">{totalDuration.toFixed(1)}s</span>
          </div>
          <div className="w-px h-6 bg-line/20" />
          <div className="flex flex-col">
            <span className="text-muted/40 mb-0.5">平均时长</span>
            <span className="text-ink">{(totalDuration / (shotsTotal || job.total_shots || shots.length || 1)).toFixed(1)}s</span>
          </div>
        </div>
      </JobCard>

      <div className="mt-5 flex flex-wrap items-center gap-2 text-[10px] font-bold tracking-wider uppercase">
        <a href="#overview" className="px-3 py-1.5 rounded-lg bg-surface/60 border border-line/10 text-muted hover:text-ink hover:bg-surface transition-colors">
          全片综述
        </a>
        <a href="#shots" className="px-3 py-1.5 rounded-lg bg-surface/60 border border-line/10 text-muted hover:text-ink hover:bg-surface transition-colors">
          镜头序列
        </a>
        <button
          onClick={toggleAllShots}
          className="px-3 py-1.5 rounded-lg text-primary/60 hover:text-primary transition-colors"
        >
          {allExpanded ? "收起全部" : "展开全部"}
        </button>
      </div>

      {/* Overview toggle if text is long */}
      {job.overview_text && (
        <div id="overview" className="mt-8 scroll-mt-24 px-1">
          <button 
            onClick={() => setOverviewOpen(!overviewOpen)}
            className="text-[10px] text-primary hover:underline font-medium"
          >
            {overviewOpen ? "收起综述" : "查看完整综述"}
          </button>
          {overviewOpen && (
            <div className="mt-2 p-4 bg-surface/50 rounded-lg text-sm text-muted leading-relaxed whitespace-pre-wrap border border-line/10">
              {job.overview_text}
            </div>
          )}
        </div>
      )}

      {/* Shot List - Compact Horizontal Strip */}
      <div id="shots" className="mt-16 space-y-12 pb-32 scroll-mt-24">
        <div className="flex items-baseline gap-4 border-b border-line/10 pb-4 px-1">
          <h3 className="text-[10px] font-bold text-muted/30 uppercase tracking-[0.4em]">镜头序列</h3>
          <span className="text-[10px] text-muted/20">{shots.length} / {shotsTotal || shots.length} 单元</span>
        </div>
        
        {shots.map((shot) => {
          const frameUrl = getFrameUrl(shot.keyframe_paths)
          const dur = shot.end_time_sec - shot.start_time_sec
          const expanded = expandedShots.has(shot.shot_number)

          return (
            <div id={`shot-${shot.shot_number}`} key={shot.id} className="perf-row-lg group flex flex-col md:flex-row gap-12 items-start border-b border-line/5 pb-12 last:border-0 scroll-mt-24">
              {/* Left Side: Frame / Clip Preview - Lowered for visual balance */}
              <div className="w-full md:w-80 lg:w-96 flex-shrink-0 md:mt-8">
                <ShotMediaPreview jobId={job.id} shot={shot} frameUrl={frameUrl} />
              </div>


              {/* Right Side: Flattened Detailed Analysis */}
              <div className="flex-1 min-w-0 space-y-4">
                <div className="flex items-center gap-4">
                  <span className="text-3xl font-serif font-light text-ink/30 group-hover:text-primary transition-colors duration-500 tracking-tighter">
                    {shot.shot_number < 10 ? `0${shot.shot_number}` : shot.shot_number}
                  </span>
                  <span className="text-[9px] font-black text-muted/20 uppercase tracking-[0.2em]">{dur.toFixed(1)}s</span>
                  <div className="h-px flex-1 bg-line/5" />
                </div>

                <p className={`text-base text-ink font-serif font-medium leading-relaxed italic tracking-tight ${expanded ? "" : "line-clamp-4"}`}>
                  {shot.overall_notes || shot.analysis_text || "尚未进行深度分析。"}
                </p>

                <button
                  onClick={() => toggleShot(shot.shot_number)}
                  className="text-[10px] text-primary/50 hover:text-primary font-bold tracking-wider uppercase transition-colors"
                >
                  {expanded ? "收起" : "展开"}
                </button>
                
                {expanded && shot.dimensions.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {shot.dimensions.map((dim) => (
                      <div key={`${shot.id}-${dim.dimension_name}`} className="flex items-center gap-2 px-2 py-1 rounded-lg bg-surface/60 border border-line/5 hover:bg-white transition-colors">
                        <span className="text-[8px] font-black text-muted/20 uppercase tracking-tighter">{dim.dimension_name}</span>
                        <span className="text-[9px] font-bold text-ink/60">{dim.label}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        })}
        {shotsTruncated && (
          <div className="text-center">
            <button
              onClick={loadMoreShots}
              disabled={loadingMoreShots}
              className="px-4 py-2 rounded-lg text-[10px] font-black text-primary/60 hover:text-primary disabled:text-muted/25 transition-colors uppercase tracking-widest"
            >
              {loadingMoreShots ? "正在加载" : "加载更多镜头"}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function ShotMediaPreview({ jobId, shot, frameUrl }: { jobId: string; shot: ShotInfo; frameUrl: string | null }) {
  const [showVideo, setShowVideo] = useState(false)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const shouldAutoPlayRef = useRef(false)
  const startSec = Math.max(0, shot.start_time_sec)
  const endSec = Math.max(startSec, shot.end_time_sec)
  const durationSec = Math.max(0, endSec - startSec)
  const videoUrl = getShotVideoUrl(jobId, startSec, endSec)

  const playShot = () => {
    shouldAutoPlayRef.current = true
    setShowVideo(true)
  }

  const showPoster = () => {
    shouldAutoPlayRef.current = false
    setShowVideo(false)
  }

  const syncStartTime = () => {
    const video = videoRef.current
    if (!video) return
    if (video.currentTime < startSec || video.currentTime >= endSec) {
      video.currentTime = startSec
    }
  }

  const stopAtEnd = () => {
    const video = videoRef.current
    if (!video) return
    if (video.currentTime >= endSec) {
      video.pause()
      video.currentTime = startSec
    }
  }

  const handleLoadedMetadata = () => {
    syncStartTime()
    if (!shouldAutoPlayRef.current) return
    void videoRef.current?.play().catch(() => {
      shouldAutoPlayRef.current = false
    })
  }

  return (
    <div className="space-y-3">
      <div className="relative w-full aspect-video rounded-2xl bg-primary-soft/5 overflow-hidden border border-line/5 shadow-sm transition-all duration-700 group-hover:shadow-md">
        {showVideo ? (
          <video
            ref={videoRef}
            src={videoUrl}
            poster={frameUrl || undefined}
            controls
            autoPlay
            preload="metadata"
            playsInline
            onLoadedMetadata={handleLoadedMetadata}
            onPlay={syncStartTime}
            onTimeUpdate={stopAtEnd}
            className="w-full h-full object-contain bg-black"
          />
        ) : (
          <button
            type="button"
            onClick={playShot}
            className="relative block w-full h-full overflow-hidden text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 group/preview"
            aria-label={`播放镜头 ${shot.shot_number}`}
          >
            {frameUrl ? (
              <img
                src={frameUrl}
                alt={`镜${shot.shot_number}`}
                loading="lazy"
                decoding="async"
                className="w-full h-full object-cover transition-transform duration-1000 group-hover:scale-105 group-hover/preview:scale-105"
              />
            ) : (
              <span className="w-full h-full flex items-center justify-center text-[9px] text-muted/20 font-serif italic tracking-widest uppercase">暂无画面</span>
            )}
            <span className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-all duration-300 group-hover/preview:bg-black/25 group-hover/preview:opacity-100 group-focus-visible/preview:bg-black/25 group-focus-visible/preview:opacity-100">
              <span className="w-12 h-12 rounded-full bg-white/90 text-primary shadow-lg flex items-center justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="currentColor" className="ml-0.5">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </span>
            </span>
          </button>
        )}
      </div>
      <div className="flex items-center justify-between gap-3 px-1">
        <span className="text-[9px] font-black text-muted/25 uppercase tracking-[0.2em]">
          {startSec.toFixed(1)}s - {endSec.toFixed(1)}s / {durationSec.toFixed(1)}s
        </span>
        {showVideo && (
          <button
            type="button"
            onClick={showPoster}
            className="text-[10px] text-primary/55 hover:text-primary font-black uppercase tracking-[0.2em] transition-colors"
          >
            查看截图
          </button>
        )}
      </div>
    </div>
  )
}

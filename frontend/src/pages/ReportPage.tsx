import { useRef, useState, useEffect } from "react"
import { useParams, Link } from "react-router-dom"
import { getJob, getReport } from "@/lib/api"
import { getFrameUrl, getShotVideoUrl, downloadFile } from "@/lib/utils"
import type { JobDetail, ShotInfo } from "@/types"
import JobCard from "@/components/JobCard"

export default function ReportPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const [job, setJob] = useState<JobDetail | null>(null)
  const [reportMd, setReportMd] = useState("")
  const [reportError, setReportError] = useState("")
  const [loading, setLoading] = useState(true)
  const [overviewOpen, setOverviewOpen] = useState(true)
  const [expandedShots, setExpandedShots] = useState<Set<number>>(new Set())

  useEffect(() => {
    if (!jobId) return
    Promise.all([
      getJob(jobId),
      getReport(jobId).catch((e) => { setReportError(e instanceof Error ? e.message : "报告加载失败"); return "" }),
    ])
      .then(([j, r]) => { setJob(j); setReportMd(r); setLoading(false) })
      .catch(() => { setLoading(false) })
  }, [jobId])

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

  const totalDuration = job.shots.reduce((sum, s) => sum + (s.end_time_sec - s.start_time_sec), 0)
  const analyzed = job.shots.filter((s) => s.analysis_text).length
  const complete = analyzed === job.shots.length
  const allExpanded = job.shots.length > 0 && expandedShots.size === job.shots.length

  const toggleShot = (shotNumber: number) => {
    setExpandedShots((prev) => {
      const next = new Set(prev)
      if (next.has(shotNumber)) next.delete(shotNumber)
      else next.add(shotNumber)
      return next
    })
  }

  const toggleAllShots = () => {
    setExpandedShots(allExpanded ? new Set() : new Set(job.shots.map((shot) => shot.shot_number)))
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
        statusLabel={complete ? "已分析" : `分析中 ${analyzed}/${job.shots.length}`}
        conclusion="分析已整理完毕，可查看各镜头维度详情。"
        primaryAction={
          <div className="flex items-center gap-2">
            <button
              onClick={() => downloadFile(reportMd, `${job.filename}_拉片报告.md`)}
              className="px-4 py-2 bg-primary text-white text-sm rounded-lg hover:bg-primary/90 transition-all shadow-sm flex items-center gap-2"
            >
              导出报告
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
            <span className="text-ink">{job.total_shots} 镜</span>
          </div>
          <div className="w-px h-6 bg-line/20" />
          <div className="flex flex-col">
            <span className="text-muted/40 mb-0.5">总时长</span>
            <span className="text-ink">{totalDuration.toFixed(1)}s</span>
          </div>
          <div className="w-px h-6 bg-line/20" />
          <div className="flex flex-col">
            <span className="text-muted/40 mb-0.5">平均时长</span>
            <span className="text-ink">{(totalDuration / (job.shots.length || 1)).toFixed(1)}s</span>
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
          <span className="text-[10px] text-muted/20">{job.shots.length} 单元</span>
        </div>
        
        {job.shots.map((shot) => {
          const frameUrl = getFrameUrl(shot.keyframe_paths)
          const dur = shot.end_time_sec - shot.start_time_sec
          const expanded = expandedShots.has(shot.shot_number)

          return (
            <div id={`shot-${shot.shot_number}`} key={shot.id} className="group flex flex-col md:flex-row gap-12 items-start border-b border-line/5 pb-12 last:border-0 scroll-mt-24">
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
                      <div key={`${shot.id}-${dim.dimension_name}`} className="flex items-center gap-2 px-2 py-1 rounded-lg bg-surface/60 border border-line/5 hover:bg-white transition-all">
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
      </div>
    </div>
  )
}

function ShotMediaPreview({ jobId, shot, frameUrl }: { jobId: string; shot: ShotInfo; frameUrl: string | null }) {
  const [showVideo, setShowVideo] = useState(false)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const startSec = Math.max(0, shot.start_time_sec)
  const endSec = Math.max(startSec, shot.end_time_sec)
  const durationSec = Math.max(0, endSec - startSec)
  const videoUrl = getShotVideoUrl(jobId, startSec, endSec)

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

  return (
    <div className="space-y-3">
      <div className="relative w-full aspect-video rounded-2xl bg-primary-soft/5 overflow-hidden border border-line/5 shadow-sm transition-all duration-700 group-hover:shadow-md">
        {showVideo ? (
          <video
            ref={videoRef}
            src={videoUrl}
            poster={frameUrl || undefined}
            controls
            preload="metadata"
            playsInline
            onLoadedMetadata={syncStartTime}
            onPlay={syncStartTime}
            onTimeUpdate={stopAtEnd}
            className="w-full h-full object-contain bg-black"
          />
        ) : frameUrl ? (
          <img src={frameUrl} alt={`镜${shot.shot_number}`} className="w-full h-full object-cover transition-transform duration-1000 group-hover:scale-105" />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-[9px] text-muted/20 font-serif italic tracking-widest uppercase">暂无画面</div>
        )}
      </div>
      <div className="flex items-center justify-between gap-3 px-1">
        <span className="text-[9px] font-black text-muted/25 uppercase tracking-[0.2em]">
          {startSec.toFixed(1)}s - {endSec.toFixed(1)}s / {durationSec.toFixed(1)}s
        </span>
        <button
          type="button"
          onClick={() => setShowVideo((prev) => !prev)}
          className="text-[10px] text-primary/55 hover:text-primary font-black uppercase tracking-[0.2em] transition-colors"
        >
          {showVideo ? "查看截图" : "播放片段"}
        </button>
      </div>
    </div>
  )
}

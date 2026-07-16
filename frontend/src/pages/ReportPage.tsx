import { useRef, useState, useEffect, useMemo } from "react"
import { useParams, Link } from "react-router-dom"
import { addReferenceBoardShot, getJobShots, getJobSummary, getJobTokenUsage, getReport, listReferenceBoard, removeReferenceBoardShot } from "@/lib/api"
import { getFrameUrl, getShotVideoUrl, downloadFile } from "@/lib/utils"
import type { JobSummary, ShotInfo, TokenUsageSummary } from "@/types"
import JobCard from "@/components/JobCard"

const SHOT_PAGE_SIZE = 80

export default function ReportPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const [job, setJob] = useState<JobSummary | null>(null)
  const [shots, setShots] = useState<ShotInfo[]>([])
  const [shotsTotal, setShotsTotal] = useState(0)
  const [tokenUsage, setTokenUsage] = useState<TokenUsageSummary | null>(null)
  const [shotsTruncated, setShotsTruncated] = useState(false)
  const [reportMd, setReportMd] = useState("")
  const [loadError, setLoadError] = useState("")
  const [exportError, setExportError] = useState("")
  const [shotsError, setShotsError] = useState("")
  const [reportLoading, setReportLoading] = useState(false)
  const [loadingMoreShots, setLoadingMoreShots] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [overviewOpen, setOverviewOpen] = useState(false)
  const [expandedShots, setExpandedShots] = useState<Set<number>>(new Set())
  const [shotSearch, setShotSearch] = useState("")
  const [copiedShot, setCopiedShot] = useState<number | null>(null)
  const [favoriteShotIds, setFavoriteShotIds] = useState<Set<number>>(new Set())
  const [favoritePendingIds, setFavoritePendingIds] = useState<Set<number>>(new Set())
  const [favoriteError, setFavoriteError] = useState("")
  const [favoriteNoticeShotId, setFavoriteNoticeShotId] = useState<number | null>(null)

  useEffect(() => {
    if (!jobId) return
    let ignore = false
    void Promise.resolve().then(() => {
      if (ignore) return
      setLoading(true)
      setReportLoading(false)
      setReportMd("")
      setLoadError("")
      setExportError("")
      setShotsError("")
      setShots([])
      setTokenUsage(null)
      setShotsTotal(0)
      setShotsTruncated(false)
      setExpandedShots(new Set())
      setShotSearch("")
      setFavoriteShotIds(new Set())
      setFavoritePendingIds(new Set())
      setFavoriteError("")
      setFavoriteNoticeShotId(null)
      Promise.all([
        getJobSummary(jobId),
        getJobShots(jobId, { limit: SHOT_PAGE_SIZE }),
        getJobTokenUsage(jobId).catch(() => null),
        listReferenceBoard({ jobId, limit: 200 }).catch((error) => {
          if (!ignore) setFavoriteError(error instanceof Error ? error.message : "参考板读取失败")
          return null
        }),
      ])
        .then(([summary, shotPage, usage, referencePage]) => {
          if (ignore) return
          setJob(summary)
          setShots(shotPage.shots)
          setTokenUsage(usage)
          setFavoriteShotIds(new Set(referencePage?.items.map((item) => item.shot_id) || []))
          setShotsTotal(shotPage.shots_total)
          setShotsTruncated(shotPage.shots_truncated)
          setLoading(false)
        })
        .catch((error) => {
          if (ignore) return
          setJob(null)
          setLoadError(error instanceof Error ? error.message : "报告加载失败")
          setLoading(false)
        })
    })
    return () => { ignore = true }
  }, [jobId, loadAttempt])

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

  const visibleShots = useMemo(() => {
    const keyword = shotSearch.trim().toLowerCase()
    if (!keyword) return shots
    return shots.filter((shot) => {
      const dimText = shot.dimensions.map((dim) => `${dim.dimension_name} ${dim.label || ""} ${dim.notes || ""}`).join(" ")
      const text = [
        String(shot.shot_number),
        shot.overall_notes || "",
        shot.analysis_text || "",
        dimText,
      ].join(" ").toLowerCase()
      return text.includes(keyword)
    })
  }, [shotSearch, shots])

  const handleExportReport = async () => {
    if (!jobId || !job) return
    setExportError("")
    let content = reportMd
    if (!content) {
      setReportLoading(true)
      try {
        content = await getReport(jobId)
        setReportMd(content)
      } catch (e) {
        setExportError(e instanceof Error ? e.message : "报告加载失败")
        return
      } finally {
        setReportLoading(false)
      }
    }
    downloadFile(content, `${job.filename}_拉片报告.md`)
  }

  const loadMoreShots = async () => {
    if (!jobId || loadingMoreShots) return
    setShotsError("")
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
      setShotsError(e instanceof Error ? e.message : "镜头加载失败")
    } finally {
      setLoadingMoreShots(false)
    }
  }

  if (!jobId) {
    return <div className="p-12 text-center text-muted">缺少分析任务编号</div>
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

  if (!job) {
    return (
      <div className="max-w-lg mx-auto p-12 text-center">
        <p className="text-sm font-medium text-ink">报告暂时无法打开</p>
        <p className="mt-2 text-xs text-muted">{loadError || "没有找到对应的分析记录"}</p>
        <div className="mt-5 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => setLoadAttempt((attempt) => attempt + 1)}
            className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary/90 transition-colors"
          >
            重试
          </button>
          <Link to="/library" className="px-4 py-2 text-xs font-bold text-muted hover:text-ink transition-colors">
            返回仓库
          </Link>
        </div>
      </div>
    )
  }

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

  const scrollToReportSection = (targetId: string) => {
    const scroll = () => {
      document.getElementById(targetId)?.scrollIntoView({ behavior: "smooth", block: "start" })
    }

    if (targetId.startsWith("shot-") && shotSearch.trim()) {
      setShotSearch("")
      window.requestAnimationFrame(() => window.requestAnimationFrame(scroll))
      return
    }

    scroll()
  }

  const copyShot = async (shot: ShotInfo) => {
    const dur = shot.end_time_sec - shot.start_time_sec
    const lines = [
      `镜头 ${shot.shot_number} / ${dur.toFixed(1)}s`,
      shot.overall_notes || shot.analysis_text || "尚未进行深度分析。",
      ...shot.dimensions.map((dim) => `- ${dim.dimension_name}: ${dim.label || "-"} ${dim.notes || ""}`.trim()),
    ]
    await navigator.clipboard.writeText(lines.join("\n"))
    setCopiedShot(shot.shot_number)
    window.setTimeout(() => setCopiedShot(null), 1600)
  }

  const toggleFavoriteShot = async (shotId: number) => {
    if (favoritePendingIds.has(shotId)) return
    const wasFavorite = favoriteShotIds.has(shotId)
    setFavoriteError("")
    setFavoriteShotIds((prev) => {
      const next = new Set(prev)
      if (wasFavorite) next.delete(shotId)
      else next.add(shotId)
      return next
    })
    setFavoritePendingIds((prev) => new Set(prev).add(shotId))
    try {
      if (wasFavorite) {
        await removeReferenceBoardShot(shotId)
        setFavoriteNoticeShotId((current) => current === shotId ? null : current)
      } else {
        await addReferenceBoardShot(shotId)
        setFavoriteNoticeShotId(shotId)
      }
    } catch (error) {
      setFavoriteShotIds((prev) => {
        const next = new Set(prev)
        if (wasFavorite) next.add(shotId)
        else next.delete(shotId)
        return next
      })
      setFavoriteError(error instanceof Error ? error.message : "参考板更新失败")
    } finally {
      setFavoritePendingIds((prev) => {
        const next = new Set(prev)
        next.delete(shotId)
        return next
      })
    }
  }

  return (
    <div className="max-w-6xl mx-auto px-6">
      <Link to={`/jobs/${jobId}`} className="text-xs text-muted hover:text-ink transition-colors inline-flex items-center gap-1 mb-6">
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
            {exportError && (
              <span className="max-w-56 text-xs text-clay" title={exportError}>导出失败：{exportError}</span>
            )}
          </div>
        }
      >
        <div className="flex items-center gap-6 text-xs text-muted font-medium tracking-normal uppercase">
          <div className="flex flex-col">
            <span className="text-muted mb-0.5">镜头总数</span>
            <span className="text-ink">{shotsTotal || job.total_shots || shots.length} 镜</span>
          </div>
          <div className="w-px h-6 bg-line/20" />
          <div className="flex flex-col">
            <span className="text-muted mb-0.5">总时长</span>
            <span className="text-ink">{totalDuration.toFixed(1)}s</span>
          </div>
          <div className="w-px h-6 bg-line/20" />
          <div className="flex flex-col">
            <span className="text-muted mb-0.5">平均时长</span>
            <span className="text-ink">{(totalDuration / (shotsTotal || job.total_shots || shots.length || 1)).toFixed(1)}s</span>
          </div>
          {tokenUsage && tokenUsage.calls > 0 && (
            <>
              <div className="w-px h-6 bg-line/20" />
              <div className="flex flex-col">
                <span className="text-muted mb-0.5">Token</span>
                <span className="text-ink">{tokenUsage.total_tokens.toLocaleString()}</span>
              </div>
              <div className="w-px h-6 bg-line/20" />
              <div className="flex flex-col">
                <span className="text-muted mb-0.5">调用</span>
                <span className="text-ink">
                  {tokenUsage.calls} 次 / 入 {tokenUsage.prompt_tokens.toLocaleString()} / 出 {tokenUsage.completion_tokens.toLocaleString()}
                </span>
              </div>
            </>
          )}
        </div>
      </JobCard>

      <div className="mt-5 flex flex-wrap items-center gap-2 text-xs font-bold tracking-normal uppercase">
        {job.overview_text && (
          <button
            type="button"
            onClick={() => scrollToReportSection("overview")}
            className="px-3 py-1.5 rounded-lg bg-surface/60 border border-line/10 text-muted hover:text-ink hover:bg-surface transition-colors"
          >
            全片综述
          </button>
        )}
        <button
          type="button"
          onClick={() => scrollToReportSection("shots")}
          className="px-3 py-1.5 rounded-lg bg-surface/60 border border-line/10 text-muted hover:text-ink hover:bg-surface transition-colors"
        >
          镜头序列
        </button>
        <button
          onClick={toggleAllShots}
          className="px-3 py-1.5 rounded-lg text-primary/60 hover:text-primary transition-colors"
        >
          {allExpanded ? "收起全部" : "展开全部"}
        </button>
      </div>

      <div className="sticky top-14 z-40 mt-6 -mx-2 px-2 py-3 bg-paper/85 backdrop-blur-md border-y border-line/5">
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <input
              value={shotSearch}
              onChange={(e) => setShotSearch(e.target.value)}
              placeholder="搜索镜头号、分析文本或维度"
              className="w-full bg-surface/70 border border-line/10 rounded-xl px-4 py-2.5 text-xs text-ink placeholder:text-muted focus:outline-none focus:border-primary/20 focus:bg-white transition-all"
            />
            {shotSearch && (
              <button
                onClick={() => setShotSearch("")}
                className="shrink-0 text-xs text-muted hover:text-ink font-bold uppercase tracking-normal"
              >
                清空
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 overflow-x-auto pb-1">
            {shots.slice(0, 60).map((shot) => (
              <button
                key={shot.id}
                type="button"
                onClick={() => scrollToReportSection(`shot-${shot.shot_number}`)}
                aria-label={`定位到镜头 ${shot.shot_number}`}
                className="shrink-0 px-2.5 py-1 rounded-lg bg-surface/60 border border-line/5 text-xs text-muted hover:text-primary hover:border-primary/20 transition-colors font-black"
              >
                {shot.shot_number < 10 ? `0${shot.shot_number}` : shot.shot_number}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Overview toggle if text is long */}
      {job.overview_text && (
        <div id="overview" className="mt-8 scroll-mt-24 px-1">
          <button 
            onClick={() => setOverviewOpen(!overviewOpen)}
            className="text-xs text-primary hover:underline font-medium"
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
      <div id="shots" className="mt-10 space-y-8 pb-20 scroll-mt-24">
        <div className="flex items-baseline gap-4 border-b border-line/10 pb-4 px-1">
          <h3 className="text-xs font-bold text-muted uppercase tracking-normal">镜头序列</h3>
          <span className="text-xs text-muted">{visibleShots.length} / {shotsTotal || shots.length} 单元</span>
        </div>
        {favoriteError && (
          <p className="px-1 text-xs text-clay">参考板同步失败：{favoriteError}</p>
        )}
        
        {visibleShots.map((shot) => {
          const frameUrl = getFrameUrl(shot.keyframe_paths)
          const dur = shot.end_time_sec - shot.start_time_sec
          const expanded = expandedShots.has(shot.shot_number)

          return (
            <div id={`shot-${shot.shot_number}`} key={shot.id} className="perf-row-lg group flex flex-col md:flex-row gap-8 items-start border-b border-line/10 pb-8 last:border-0 scroll-mt-24">
              {/* Left Side: Frame / Clip Preview - Lowered for visual balance */}
              <div className="w-full md:w-72 lg:w-80 flex-shrink-0 md:mt-5">
                <ShotMediaPreview jobId={job.id} shot={shot} frameUrl={frameUrl} />
              </div>


              {/* Right Side: Flattened Detailed Analysis */}
              <div className="flex-1 min-w-0 space-y-4">
                <div className="flex items-center gap-4">
                  <span className="text-3xl font-serif font-light text-ink/50 group-hover:text-primary transition-colors duration-500 tracking-normal">
                    {shot.shot_number < 10 ? `0${shot.shot_number}` : shot.shot_number}
                  </span>
                  <span className="text-xs font-black text-muted uppercase tracking-normal">{dur.toFixed(1)}s</span>
                  <div className="h-px flex-1 bg-line/5" />
                </div>

                <p className={`text-base text-ink font-serif font-medium leading-relaxed italic tracking-normal ${expanded ? "" : "line-clamp-4"}`}>
                  {shot.overall_notes || shot.analysis_text || "尚未进行深度分析。"}
                </p>

                <div className="flex items-center gap-4">
                  <button
                    onClick={() => toggleShot(shot.shot_number)}
                    className="text-xs text-primary hover:text-ink font-bold tracking-normal uppercase transition-colors"
                  >
                    {expanded ? "收起" : "展开"}
                  </button>
                  <button
                    onClick={() => void copyShot(shot)}
                    className="text-xs text-muted hover:text-ink font-bold tracking-normal uppercase transition-colors"
                  >
                    {copiedShot === shot.shot_number ? "已复制" : "复制镜头"}
                  </button>
                  <button
                    type="button"
                    aria-pressed={favoriteShotIds.has(shot.id)}
                    disabled={favoritePendingIds.has(shot.id)}
                    onClick={() => void toggleFavoriteShot(shot.id)}
                    className={`text-xs font-bold uppercase tracking-normal transition-colors disabled:opacity-50 ${
                      favoriteShotIds.has(shot.id) ? "text-primary" : "text-muted hover:text-primary"
                    }`}
                    title={favoriteShotIds.has(shot.id) ? "从参考板移除" : "收藏到参考板"}
                  >
                    {favoriteShotIds.has(shot.id) ? "★ 已收藏" : "☆ 收藏"}
                  </button>
                </div>
                {favoriteNoticeShotId === shot.id && favoriteShotIds.has(shot.id) && (
                  <div role="status" className="flex flex-wrap items-center gap-2 rounded-md border border-sage/25 bg-sage/5 px-3 py-2 text-xs font-medium text-sage">
                    <span>已加入参考板</span>
                    <Link
                      to={`/storyboard?referenceShot=${shot.id}`}
                      className="font-bold text-primary hover:text-ink focus-visible:outline-2 focus-visible:outline-primary/35"
                    >
                      去生成快速分镜 →
                    </Link>
                  </div>
                )}
                
                {expanded && shot.dimensions.length > 0 && (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-5 gap-y-3 pt-1">
                    {shot.dimensions.map((dim) => (
                      <div key={`${shot.id}-${dim.dimension_name}`} className="border-l-2 border-line/20 pl-3 py-0.5">
                        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                          <span className="text-xs font-black text-muted uppercase tracking-normal">{dim.dimension_name}</span>
                          {dim.label && <span className="text-xs font-bold text-ink">{dim.label}</span>}
                          {dim.score != null && <span className="text-xs text-muted">{dim.score} 分</span>}
                        </div>
                        {dim.notes && <p className="mt-1 text-xs leading-relaxed text-muted">{dim.notes}</p>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        })}
        {visibleShots.length === 0 && (
          <div className="py-24 text-center">
            <p className="text-sm text-muted font-serif italic tracking-normal">没有匹配的镜头。</p>
          </div>
        )}
        {shotsTruncated && (
          <div className="text-center space-y-2">
            {shotsError && <p className="text-xs text-clay">加载更多失败：{shotsError}</p>}
            <button
              onClick={loadMoreShots}
              disabled={loadingMoreShots}
              className="px-4 py-2 rounded-lg text-xs font-black text-primary/60 hover:text-primary disabled:text-muted transition-colors uppercase tracking-normal"
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
  const [playbackError, setPlaybackError] = useState("")
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const shouldAutoPlayRef = useRef(false)
  const startSec = Math.max(0, shot.start_time_sec)
  const endSec = Math.max(startSec, shot.end_time_sec)
  const durationSec = Math.max(0, endSec - startSec)
  const videoUrl = getShotVideoUrl(jobId, startSec, endSec)

  const playShot = () => {
    shouldAutoPlayRef.current = true
    setPlaybackError("")
    setShowVideo(true)
  }

  const showPoster = () => {
    shouldAutoPlayRef.current = false
    setShowVideo(false)
  }

  const handlePlaybackError = () => {
    shouldAutoPlayRef.current = false
    setShowVideo(false)
    setPlaybackError("片段读取失败")
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
            onError={handlePlaybackError}
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
              <span className="w-full h-full flex items-center justify-center text-xs text-muted font-serif italic tracking-normal uppercase">暂无画面</span>
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
        <div className="min-w-0">
          <span className="text-xs font-black text-muted uppercase tracking-normal">
            {startSec.toFixed(1)}s - {endSec.toFixed(1)}s / {durationSec.toFixed(1)}s
          </span>
          {playbackError && <p className="mt-1 text-xs text-clay">{playbackError}</p>}
        </div>
        {showVideo && (
          <button
            type="button"
            onClick={showPoster}
            className="text-xs text-primary/55 hover:text-primary font-black uppercase tracking-normal transition-colors"
          >
            查看截图
          </button>
        )}
        {!showVideo && playbackError && (
          <button
            type="button"
            onClick={playShot}
            className="shrink-0 text-xs text-primary hover:text-primary/80 font-black uppercase tracking-normal transition-colors"
          >
            重试播放
          </button>
        )}
      </div>
    </div>
  )
}

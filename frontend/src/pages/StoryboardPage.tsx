import { useState, useEffect, useCallback, useMemo, useRef } from "react"
import { useSearchParams } from "react-router-dom"
import { listJobs, generateStoryboardStream, listStoryboards, listStoryboardGenerations, getStoryboard, deleteStoryboard, retryStoryboardShotImage, listReferenceBoard, removeReferenceBoardShot } from "@/lib/api"
import StatusBean from "@/components/StatusBean"
import JobCard from "@/components/JobCard"
import Logo from "@/components/Logo"
import { categorySort, downloadFile, getFrameUrl, getStoryboardImageUrl } from "@/lib/utils"
import type { JobInfo, ReferenceBoardItem, StoryboardResult, StoryboardHistoryItem, StoryboardDetail, StoryboardGenerationTask } from "@/types"

const ACTIVE_STORYBOARD_TASK_ID_KEY = "storyboard_active_task_id"

export default function StoryboardPage() {
  const [searchParams] = useSearchParams()
  const requestedReferenceShot = Number.parseInt(searchParams.get("referenceShot") || "", 10)
  const [jobs, setJobs] = useState<JobInfo[]>([])
  const [brief, setBrief] = useState(() => localStorage.getItem("storyboard_brief") || "")
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => {
    try { const raw = localStorage.getItem("storyboard_selected_ids"); return raw ? new Set(JSON.parse(raw)) : new Set() } catch { return new Set() }
  })
  const [referenceMode, setReferenceMode] = useState<"board" | "jobs">(() => (
    Number.isInteger(requestedReferenceShot) || localStorage.getItem("storyboard_reference_mode") !== "jobs" ? "board" : "jobs"
  ))
  const [referenceBoard, setReferenceBoard] = useState<ReferenceBoardItem[]>([])
  const [selectedShotIds, setSelectedShotIds] = useState<Set<number>>(() => {
    try {
      const raw = localStorage.getItem("storyboard_selected_shot_ids")
      const selected = raw ? new Set<number>(JSON.parse(raw) as number[]) : new Set<number>()
      if (Number.isInteger(requestedReferenceShot)) selected.add(requestedReferenceShot)
      return selected
    } catch {
      return Number.isInteger(requestedReferenceShot) ? new Set([requestedReferenceShot]) : new Set()
    }
  })
  const [boardSearch, setBoardSearch] = useState("")
  const [boardError, setBoardError] = useState("")
  const [targetDur, setTargetDur] = useState(() => localStorage.getItem("storyboard_target_dur") || "")
  const [generating, setGenerating] = useState(false)
  const [generationProgress, setGenerationProgress] = useState("")
  const [result, setResult] = useState<StoryboardResult | null>(null)
  const [error, setError] = useState("")
  const [history, setHistory] = useState<StoryboardHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState("")
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [showAllHistory, setShowAllHistory] = useState(false)
  const [generationTasks, setGenerationTasks] = useState<StoryboardGenerationTask[]>([])
  const [retryingImageShots, setRetryingImageShots] = useState<Set<number>>(new Set())
  const [activeTaskId, setActiveTaskId] = useState(() => localStorage.getItem(ACTIVE_STORYBOARD_TASK_ID_KEY) || "")
  const generationAbortRef = useRef<AbortController | null>(null)
  const autoOpenedTaskRef = useRef<string | null>(null)
  const hasActiveGenerationTask = generating || Boolean(activeTaskId) || generationTasks.some((task) => isStoryboardTaskActive(task.status))
  const generationPollingIntervalMs = hasActiveGenerationTask ? 2000 : 10000

  const load = useCallback(async () => {
    try {
      const j = await listJobs()
      setJobs(j)
      // Prune stale IDs that no longer exist in the job list
      const validIds = new Set(j.map((x) => x.id))
      setSelectedIds((prev) => {
        const filtered = new Set([...prev].filter((id) => validIds.has(id)))
        return filtered.size !== prev.size ? filtered : prev
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : "参考素材加载失败")
    }
  }, [])
  const loadHistory = useCallback(async () => {
    setHistoryError("")
    try {
      setHistory(await listStoryboards())
    } catch (e) {
      console.error("Failed to load storyboard history", e)
      setHistoryError(e instanceof Error ? e.message : "分镜历史加载失败")
    }
  }, [])

  const loadReferenceBoard = useCallback(async () => {
    setBoardError("")
    try {
      const page = await listReferenceBoard({ limit: 200 })
      setReferenceBoard(page.items)
      const validIds = new Set(page.items.map((item) => item.shot_id))
      setSelectedShotIds((prev) => {
        const filtered = new Set([...prev].filter((id) => validIds.has(id)))
        if (filtered.size > 0 || prev.size > 0 || page.items.length === 0) return filtered
        return new Set(page.items.slice(0, 12).map((item) => item.shot_id))
      })
    } catch (e) {
      setBoardError(e instanceof Error ? e.message : "参考板加载失败")
    }
  }, [])

  const loadGenerationTasks = useCallback(async () => {
    try {
      const tasks = await listStoryboardGenerations()
      setGenerationTasks((prev) => storyboardTasksEqual(prev, tasks) ? prev : tasks)

      const storedTaskId = localStorage.getItem(ACTIVE_STORYBOARD_TASK_ID_KEY)
      const activeTask = storedTaskId ? tasks.find((task) => task.id === storedTaskId) : undefined
      if (activeTask?.status === "completed" && activeTask.storyboard_id && autoOpenedTaskRef.current !== activeTask.id) {
        autoOpenedTaskRef.current = activeTask.id
        localStorage.removeItem(ACTIVE_STORYBOARD_TASK_ID_KEY)
        setActiveTaskId("")
        const detail: StoryboardDetail = await getStoryboard(activeTask.storyboard_id)
        setResult({
          id: detail.id,
          title: detail.title,
          total_duration_sec: detail.total_duration_sec,
          shots: detail.shots,
          full_notes: detail.full_notes,
        })
        setBrief(detail.brief)
        setTargetDur(detail.total_duration_sec?.toString() || "")
        setSelectedIds(new Set(detail.reference_job_ids))
        setSelectedShotIds(new Set(detail.reference_shot_ids || []))
        setReferenceMode(detail.reference_shot_ids?.length ? "board" : "jobs")
        await loadHistory()
        window.scrollTo({ top: 0, behavior: "smooth" })
      } else if (activeTask?.status === "failed") {
        localStorage.removeItem(ACTIVE_STORYBOARD_TASK_ID_KEY)
        setActiveTaskId("")
      }
    } catch (e) {
      console.error("Failed to load storyboard generations", e)
    }
  }, [loadHistory])

  useEffect(() => {
    void Promise.resolve().then(async () => {
      await load()
      await loadReferenceBoard()
      await loadHistory()
      await loadGenerationTasks()
    })
  }, [load, loadHistory, loadGenerationTasks, loadReferenceBoard])
  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadGenerationTasks()
    }, generationPollingIntervalMs)
    return () => window.clearInterval(timer)
  }, [generationPollingIntervalMs, loadGenerationTasks])
  useEffect(() => () => generationAbortRef.current?.abort(), [])
  useEffect(() => {
    if (result) return
    const timer = window.setTimeout(() => {
      localStorage.setItem("storyboard_brief", brief)
    }, 400)
    return () => window.clearTimeout(timer)
  }, [brief, result])
  useEffect(() => {
    if (result) return
    const timer = window.setTimeout(() => {
      localStorage.setItem("storyboard_target_dur", targetDur)
    }, 400)
    return () => window.clearTimeout(timer)
  }, [result, targetDur])
  useEffect(() => { localStorage.setItem("storyboard_selected_ids", JSON.stringify([...selectedIds])) }, [selectedIds])
  useEffect(() => { localStorage.setItem("storyboard_selected_shot_ids", JSON.stringify([...selectedShotIds])) }, [selectedShotIds])
  useEffect(() => { localStorage.setItem("storyboard_reference_mode", referenceMode) }, [referenceMode])

  const referenceJobs = useMemo(() => jobs.filter((j) => j.status === "completed"), [jobs])
  const selectedReferenceJobs = useMemo(
    () => referenceJobs.filter((job) => selectedIds.has(job.id)),
    [referenceJobs, selectedIds],
  )
  const visibleHistory = useMemo(() => showAllHistory ? history : history.slice(0, 3), [history, showAllHistory])
  const visibleReferenceBoard = useMemo(() => {
    const keyword = boardSearch.trim().toLowerCase()
    if (!keyword) return referenceBoard
    return referenceBoard.filter((item) => [
      item.job_filename,
      item.job_category || "",
      String(item.shot_number),
      item.analysis_text || item.overall_notes || "",
    ].join(" ").toLowerCase().includes(keyword))
  }, [boardSearch, referenceBoard])
  const activeGenerationTasks = useMemo(
    () => generationTasks.filter((task) => isStoryboardTaskActive(task.status)),
    [generationTasks],
  )
  const visibleGenerationTasks = useMemo(
    () => (
      activeGenerationTasks.length > 0
        ? activeGenerationTasks
        : generationTasks.filter((task) => task.status === "failed" || task.id === activeTaskId).slice(0, 2)
    ),
    [activeGenerationTasks, activeTaskId, generationTasks],
  )

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const toggleShotSelect = (id: number) => {
    setSelectedShotIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const handleRemoveReferenceShot = async (id: number) => {
    setBoardError("")
    try {
      await removeReferenceBoardShot(id)
      setReferenceBoard((prev) => prev.filter((item) => item.shot_id !== id))
      setSelectedShotIds((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    } catch (e) {
      setBoardError(e instanceof Error ? e.message : "参考镜头移除失败")
    }
  }

  // Group references by category
  const referenceGroups = useMemo(() => {
    const groups: Record<string, JobInfo[]> = {}
    referenceJobs.forEach((job) => {
      const cat = job.category || "未分类"
      if (!groups[cat]) groups[cat] = []
      groups[cat].push(job)
    })
    return groups
  }, [referenceJobs])

  const toggleCategory = (catName: string) => {
    const groupIds = referenceGroups[catName].map(j => j.id)
    const allSelected = groupIds.every(id => selectedIds.has(id))
    
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (allSelected) {
        groupIds.forEach(id => next.delete(id))
      } else {
        groupIds.forEach(id => next.add(id))
      }
      return next
    })
  }

  const handleGenerate = async () => {
    if (!brief.trim()) { setError("请输入创作需求"); return }
    if (brief.trim().length > 5000) { setError("创作需求不能超过 5000 字"); return }
    if (referenceMode === "board" && selectedShotIds.size === 0) { setError("请从参考板选择镜头"); return }
    if (referenceMode === "jobs" && selectedIds.size === 0) { setError("请选择参考素材"); return }
    if (referenceMode === "board" && selectedShotIds.size > 20) { setError("一次最多选择 20 个参考镜头"); return }
    if (referenceMode === "jobs" && selectedIds.size > 20) { setError("一次最多选择 20 个参考素材"); return }
    setError("")
    setGenerating(true)
    setGenerationProgress("")
    generationAbortRef.current?.abort()
    const controller = new AbortController()
    generationAbortRef.current = controller
    const parsed = parseFloat(targetDur)
    const dur = !isNaN(parsed) ? Math.round(parsed) : undefined
    const taskId = createStoryboardTaskId()
    const referenceJobIds = referenceMode === "jobs" ? [...selectedIds] : []
    const referenceShotIds = referenceMode === "board" ? [...selectedShotIds] : []
    const startedAt = new Date().toISOString()
    setActiveTaskId(taskId)
    localStorage.setItem(ACTIVE_STORYBOARD_TASK_ID_KEY, taskId)
    setGenerationTasks((prev) => [
      {
        id: taskId,
        brief,
        reference_job_ids: referenceJobIds,
        reference_shot_ids: referenceShotIds,
        target_duration_sec: dur ?? null,
        status: "queued",
        progress: 0.02,
        message: "已加入生成队列",
        storyboard_id: null,
        error_message: null,
        created_at: startedAt,
        updated_at: startedAt,
      },
      ...prev.filter((task) => task.id !== taskId),
    ])

    await generateStoryboardStream(brief, referenceJobIds, dur, {
      onStarted: (task) => {
        setActiveTaskId(task.id)
        localStorage.setItem(ACTIVE_STORYBOARD_TASK_ID_KEY, task.id)
        setGenerationTasks((prev) => [task, ...prev.filter((item) => item.id !== task.id)])
      },
      onProgress: (msg) => {
        if (!controller.signal.aborted) setGenerationProgress(msg)
      },
      onComplete: (res) => {
        if (controller.signal.aborted) return
        setResult(res)
        setGenerating(false)
        setGenerationProgress("")
        generationAbortRef.current = null
        setActiveTaskId("")
        localStorage.removeItem(ACTIVE_STORYBOARD_TASK_ID_KEY)
        loadHistory()
        loadGenerationTasks()
        localStorage.removeItem("storyboard_brief")
        localStorage.removeItem("storyboard_target_dur")
        localStorage.removeItem("storyboard_selected_ids")
        localStorage.removeItem("storyboard_selected_shot_ids")
      },
      onError: (msg) => {
        if (controller.signal.aborted) return
        setError(msg)
        setGenerating(false)
        setGenerationProgress("")
        generationAbortRef.current = null
        if (msg.includes("任务已存在")) {
          void loadGenerationTasks()
          return
        }
        setActiveTaskId("")
        localStorage.removeItem(ACTIVE_STORYBOARD_TASK_ID_KEY)
        loadGenerationTasks()
      },
    }, controller.signal, taskId, {
      referenceShotIds,
      generateImages: referenceMode === "jobs",
    })
  }

  const handleLoadHistory = async (id: string) => {
    setHistoryLoading(true)
    setHistoryError("")
    try {
      const detail: StoryboardDetail = await getStoryboard(id)
      setResult({ id: detail.id, title: detail.title, total_duration_sec: detail.total_duration_sec, shots: detail.shots, full_notes: detail.full_notes })
      // Sync state to left panel for 'Remix' mode
      setBrief(detail.brief)
      setTargetDur(detail.total_duration_sec?.toString() || "")
      setSelectedIds(new Set(detail.reference_job_ids))
      setSelectedShotIds(new Set(detail.reference_shot_ids || []))
      setReferenceMode(detail.reference_shot_ids?.length ? "board" : "jobs")
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : "分镜读取失败")
    }
    finally { setHistoryLoading(false) }
  }

  const handleReset = () => {
    if (hasActiveGenerationTask) {
      setError("分镜仍在后台生成，完成后再重置")
      return
    }
    setBrief("")
    setTargetDur("")
    setSelectedIds(new Set())
    setSelectedShotIds(new Set())
    setResult(null)
    setError("")
    generationAbortRef.current?.abort()
    generationAbortRef.current = null
    setGenerating(false)
    setGenerationProgress("")
    setActiveTaskId("")
    localStorage.removeItem(ACTIVE_STORYBOARD_TASK_ID_KEY)
    localStorage.removeItem("storyboard_brief")
    localStorage.removeItem("storyboard_target_dur")
    localStorage.removeItem("storyboard_selected_ids")
    localStorage.removeItem("storyboard_selected_shot_ids")
  }

  const exportToMarkdown = () => {
    if (!result) return
    let md = `# ${result.title}\n\n`
    md += `> **创作需求**: ${brief}\n`
    md += `> **总镜头数**: ${result.shots.length}\n`
    md += `> **预估时长**: ${result.total_duration_sec}s\n\n`
    
    if (result.full_notes) {
      md += `## 创作说明\n${result.full_notes}\n\n`
    }
    
    md += `## 分镜脚本\n\n`
    md += `| 镜号 | 分镜图 | 时长 | 画面描述 | 机位 | 生图提示词 | 参考来源 |\n`
    md += `| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n`
    
    result.shots.forEach(s => {
      const imageSrc = getStoryboardImageUrl(s.image_url, s.image_path)
      const imageCell = imageSrc ? `![分镜图 ${s.shot_number}](${imageSrc})` : "-"
      const cells = [
        s.shot_number,
        imageCell,
        `${s.duration_sec}s`,
        s.description,
        s.camera_movement,
        s.image_prompt || "-",
        s.reference_from,
      ]
      md += `| ${cells.map(escapeMarkdownCell).join(" | ")} |\n`
    })
    
    md += `\n---\n`
    md += `*Generated by Framebench / 拉片工作台*\n`

    downloadFile(md, `${result.title.replace(/\s+/g, '_')}_分镜脚本.md`)
  }

  const handleDeleteHistory = async (id: string) => {
    if (!window.confirm("确定删除？")) return
    try { await deleteStoryboard(id); loadHistory() } catch (e) { setError(e instanceof Error ? e.message : "删除失败") }
  }

  const handleRetryShotImage = async (shotNumber: number) => {
    if (!result?.id) {
      setError("请先从历史记录打开已保存的分镜，再重试单张生图")
      return
    }
    setError("")
    setRetryingImageShots((prev) => new Set(prev).add(shotNumber))
    setResult((prev) => prev ? {
      ...prev,
      shots: prev.shots.map((shot) => shot.shot_number === shotNumber ? { ...shot, image_status: "generating", image_error: null } : shot),
    } : prev)
    try {
      const updated = await retryStoryboardShotImage(result.id, shotNumber)
      setResult((prev) => prev ? {
        ...prev,
        shots: prev.shots.map((shot) => shot.shot_number === shotNumber ? { ...shot, ...updated } : shot),
      } : prev)
    } catch (e) {
      const message = e instanceof Error ? e.message : "分镜图重试失败"
      setError(message)
      setResult((prev) => prev ? {
        ...prev,
        shots: prev.shots.map((shot) => shot.shot_number === shotNumber ? { ...shot, image_status: "failed", image_error: message } : shot),
      } : prev)
    } finally {
      setRetryingImageShots((prev) => {
        const next = new Set(prev)
        next.delete(shotNumber)
        return next
      })
    }
  }

  const selectedReferenceCount = referenceMode === "board" ? selectedShotIds.size : selectedIds.size
  const generateDisabledReason = generating
    ? "生成任务正在运行"
    : !brief.trim()
      ? "请先填写创作需求"
      : brief.trim().length > 5000
        ? "创作需求不能超过 5000 字"
        : selectedReferenceCount === 0
          ? referenceMode === "board" ? "请从参考板选择至少一个镜头" : "请选择至少一条参考素材"
          : selectedReferenceCount > 20
            ? `一次最多选择 20 个${referenceMode === "board" ? "参考镜头" : "参考素材"}`
            : ""
  const generateDisabled = Boolean(generateDisabledReason)

  return (
    <div className="max-w-6xl mx-auto px-4 pb-32">
      {/* Top Section: Horizontal Creation Bar - Visible only when not viewing a result */}
      {!result && (
        <div className="mb-10 rounded-lg border border-line/20 bg-surface p-6 shadow-[0_8px_24px_rgb(0,0,0,0.035)] transition-shadow focus-within:shadow-[0_14px_32px_rgba(47,39,34,0.05)] animate-in fade-in slide-in-from-top-4 duration-500">
          <ol className="mb-6 grid grid-cols-3 gap-2 border-b border-line/20 pb-5" aria-label="快速分镜步骤">
            {[
              ["1", "创作需求"],
              ["2", "选择参考"],
              ["3", "生成"],
            ].map(([number, label]) => (
              <li key={number} className="flex min-w-0 items-center gap-2 text-xs font-semibold text-muted">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-line/40 bg-paper text-ink">{number}</span>
                <span className="truncate">{label}</span>
              </li>
            ))}
          </ol>
          <div className="flex flex-col items-start gap-6 md:flex-row">
            
            {/* Brief Input - Grows to fill */}
            <div className="flex-1 w-full space-y-4">
              <div className="flex items-center justify-between px-1">
                <h3 className="text-xs font-bold text-muted uppercase tracking-normal">创作需求</h3>
                <button 
                  onClick={handleReset}
                  className="text-xs text-primary hover:text-ink transition-colors flex items-center gap-1.5 font-black uppercase tracking-normal"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
                  重置
                </button>
              </div>
              <textarea
                value={brief}
                onChange={(e) => setBrief(e.target.value)}
                placeholder="在这里输入你的创作意图..."
                className="h-28 w-full resize-none rounded-md bg-transparent px-1 text-xl text-ink placeholder:text-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 leading-relaxed font-serif italic"
              />
            </div>

            {/* Configuration & Action - Fixed width or compact */}
            <div className="w-full space-y-6 pt-1 md:w-80">
              <div className="space-y-4">
                <h3 className="text-xs font-bold text-muted uppercase tracking-normal">选择参考</h3>
                <div className="flex flex-col gap-3">
                  <div className="grid grid-cols-2 gap-1 p-1 bg-paper border border-line/10 rounded-lg">
                    <button
                      type="button"
                      onClick={() => setReferenceMode("board")}
                      className={`py-2 rounded-md text-xs font-bold transition-colors ${
                        referenceMode === "board" ? "bg-primary text-white" : "text-muted hover:text-ink"
                      }`}
                    >
                      参考镜头
                    </button>
                    <button
                      type="button"
                      onClick={() => setReferenceMode("jobs")}
                      className={`py-2 rounded-md text-xs font-bold transition-colors ${
                        referenceMode === "jobs" ? "bg-primary text-white" : "text-muted hover:text-ink"
                      }`}
                    >
                      整片风格
                    </button>
                  </div>
                  {referenceMode === "board" ? (
                    <div className="space-y-2">
                      <input
                        value={boardSearch}
                        onChange={(event) => setBoardSearch(event.target.value)}
                        placeholder="搜索参考镜头"
                        className="w-full px-3 py-2.5 bg-paper border border-line/10 rounded-lg text-xs text-ink placeholder:text-muted focus:outline-none focus:border-primary/30"
                      />
                      <div className="max-h-56 overflow-y-auto border border-line/10 rounded-lg bg-paper/60 divide-y divide-line/10 custom-scrollbar">
                        {boardError && (
                          <div className="px-3 py-3 flex items-center justify-between gap-3 text-xs text-clay">
                            <span className="truncate">{boardError}</span>
                            <button type="button" onClick={() => void loadReferenceBoard()} className="shrink-0 font-bold hover:text-ink">重试</button>
                          </div>
                        )}
                        {!boardError && visibleReferenceBoard.length === 0 && (
                          <p className="px-3 py-5 text-center text-xs text-muted">参考板为空</p>
                        )}
                        {visibleReferenceBoard.map((item) => {
                          const frameUrl = getFrameUrl(item.keyframe_paths)
                          const selected = selectedShotIds.has(item.shot_id)
                          return (
                            <div key={item.shot_id} className={`flex items-center gap-2 p-2 ${selected ? "bg-primary-soft/30" : ""}`}>
                              <button
                                type="button"
                                onClick={() => toggleShotSelect(item.shot_id)}
                                className="min-w-0 flex-1 flex items-center gap-3 text-left"
                                aria-pressed={selected}
                              >
                                <span className={`w-4 h-4 shrink-0 rounded border flex items-center justify-center ${selected ? "bg-primary border-primary text-white" : "border-line bg-white"}`}>
                                  {selected ? "✓" : ""}
                                </span>
                                <span className="w-16 aspect-video shrink-0 rounded overflow-hidden bg-surface">
                                  {frameUrl && <img src={frameUrl} alt="" loading="lazy" className="w-full h-full object-cover" />}
                                </span>
                                <span className="min-w-0">
                                  <span className="block text-xs font-bold text-ink truncate">{item.job_filename}</span>
                                  <span className="block text-xs text-muted">镜头 {item.shot_number} / {(item.end_time_sec - item.start_time_sec).toFixed(1)}s</span>
                                </span>
                              </button>
                              <button
                                type="button"
                                onClick={() => void handleRemoveReferenceShot(item.shot_id)}
                                className="shrink-0 w-7 h-7 text-muted hover:text-clay transition-colors"
                                title="从参考板移除"
                                aria-label={`从参考板移除镜头 ${item.shot_number}`}
                              >
                                ×
                              </button>
                            </div>
                          )
                        })}
                      </div>
                      <p className="px-1 text-xs text-muted">已选择 {selectedShotIds.size} / {referenceBoard.length} 镜</p>
                    </div>
                  ) : (
                    <>
                      <div className="relative group/ref">
                        <button className="w-full flex items-center justify-between px-4 py-3 bg-paper rounded-lg border border-line/10 text-xs text-ink font-bold hover:border-primary/20 transition-all uppercase tracking-normal text-left">
                          <span className="truncate">
                            {selectedIds.size > 0 ? `已选择 ${selectedIds.size} 个参考` : "选择参考素材"}
                          </span>
                          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="text-muted"><path d="m6 9 6 6 6-6"/></svg>
                        </button>
                        <div className="absolute top-full left-0 right-0 mt-3 bg-surface border border-line/10 rounded-lg shadow-2xl z-[100] opacity-0 invisible group-focus-within/ref:opacity-100 group-focus-within/ref:visible transition-all max-h-[26rem] overflow-y-auto p-5 custom-scrollbar text-left">
                          <div className="space-y-6">
                            {Object.entries(referenceGroups).sort(categorySort).map(([catName, items]) => {
                              const allSelected = items.every(j => selectedIds.has(j.id))
                              const someSelected = items.some(j => selectedIds.has(j.id)) && !allSelected
                              return (
                                <div key={catName} className="space-y-2">
                                  <div className="flex items-center justify-between px-1 group/cat cursor-pointer" onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggleCategory(catName); }}>
                                    <h4 className="text-xs font-black text-muted uppercase tracking-normal group-hover/cat:text-primary transition-colors">{catName}</h4>
                                    <div className={`w-3.5 h-3.5 rounded border border-line transition-all flex items-center justify-center ${allSelected ? "bg-primary border-primary" : someSelected ? "bg-primary/30 border-primary/20" : "bg-white"}`}>
                                      {allSelected && <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="4" className="w-2.5 h-2.5"><polyline points="20 6 9 17 4 12"/></svg>}
                                      {someSelected && <div className="w-1.5 h-0.5 bg-white rounded-full" />}
                                    </div>
                                  </div>
                                  <div className="space-y-1">
                                    {items.map(job => (
                                      <button
                                        type="button"
                                        key={job.id}
                                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggleSelect(job.id); }}
                                        className={`block w-full px-3 py-2 rounded-md text-xs text-left transition-colors ${
                                          selectedIds.has(job.id) ? "bg-primary-soft/30 text-ink font-bold" : "text-muted hover:bg-paper"
                                        }`}
                                      >
                                        {job.filename}
                                      </button>
                                    ))}
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      </div>
                      {selectedReferenceJobs.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {selectedReferenceJobs.slice(0, 4).map((job) => (
                            <button
                              key={job.id}
                              type="button"
                              onClick={() => toggleSelect(job.id)}
                              className="flex max-w-full items-center gap-1.5 rounded-md bg-primary-soft/25 px-3 py-1.5 text-xs text-ink/70 hover:bg-clay/5 hover:text-clay focus-visible:ring-2 focus-visible:ring-primary/30 transition-colors"
                              title={job.filename}
                              aria-label={`移除参考素材 ${job.filename}`}
                            >
                              <span className="truncate">{job.filename}</span>
                              <span aria-hidden="true">×</span>
                            </button>
                          ))}
                          {selectedReferenceJobs.length > 4 && (
                            <span className="px-3 py-1.5 text-xs text-muted font-bold">+{selectedReferenceJobs.length - 4}</span>
                          )}
                        </div>
                      )}
                    </>
                  )}
                  <div className="space-y-2 border-t border-line/20 pt-4">
                    <h3 className="text-xs font-bold text-muted uppercase tracking-normal">生成</h3>
                    <div className="flex items-center gap-3">
                      <div className="flex flex-1 items-center gap-2 rounded-md border border-line/25 bg-paper px-3 py-3 transition-colors focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-primary/20">
                        <input
                          type="number"
                          min="1"
                          step="1"
                          value={targetDur}
                          onChange={(e) => setTargetDur(e.target.value)}
                          placeholder="目标时长"
                          aria-label="目标时长（秒）"
                          className="w-full bg-transparent text-xs font-bold text-ink focus:outline-none"
                        />
                        <span className="text-xs font-semibold text-muted">秒</span>
                      </div>
                      <button
                        type="button"
                        onClick={handleGenerate}
                        disabled={generateDisabled}
                        aria-describedby={generateDisabledReason ? "storyboard-generate-requirement" : undefined}
                        className={`min-h-11 flex-[2] rounded-md px-4 text-sm font-bold transition-colors focus-visible:ring-2 focus-visible:ring-primary/35 ${
                          generateDisabled
                            ? "cursor-not-allowed bg-line/45 text-muted"
                            : "bg-primary text-white shadow-sm hover:bg-primary/90"
                        }`}
                      >
                        {generating ? "正在生成" : "生成快速分镜"}
                      </button>
                    </div>
                    {generateDisabledReason && (
                      <p id="storyboard-generate-requirement" className="text-xs font-medium text-clay">
                        {generateDisabledReason}
                      </p>
                    )}
                  </div>
                </div>
              </div>
              {generating && generationProgress && (
                <div className="flex items-center gap-2 px-1 animate-in fade-in duration-300">
                  <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                  <p className="text-xs text-muted font-bold tracking-normal text-left">{generationProgress}</p>
                </div>
              )}
              {error && <p className="text-xs text-clay px-1 font-bold italic animate-pulse text-left">● {error}</p>}
            </div>
          </div>
        </div>
	      )}

	      {!result && visibleGenerationTasks.length > 0 && (
	        <div className="mb-16 px-1 animate-in fade-in slide-in-from-top-2 duration-500">
	          <div className="flex items-center justify-between mb-5">
	            <div className="flex items-center gap-4">
	              <h3 className="text-xs font-black text-muted uppercase tracking-normal">生成队列</h3>
	              <div className="h-px w-16 bg-line/10" />
	            </div>
	            <span className="text-xs text-muted font-black uppercase tracking-normal">
	              {activeGenerationTasks.length > 0 ? `${activeGenerationTasks.length} 个任务运行中` : "最近任务"}
	            </span>
	          </div>
	          <div className="space-y-3">
	            {visibleGenerationTasks.map((task) => {
	              const progressPct = Math.max(4, Math.min(100, Math.round((task.progress || 0) * 100)))
	              const isActive = isStoryboardTaskActive(task.status)
	              const isFailed = task.status === "failed"
	              return (
	                <div key={task.id} className="perf-row-sm rounded-2xl bg-surface/35 border border-line/5 px-5 py-4">
	                  <div className="flex items-start justify-between gap-5">
	                    <div className="min-w-0 flex-1">
	                      <div className="flex items-center gap-3 mb-2">
	                        <span className={`w-1.5 h-1.5 rounded-full ${isFailed ? "bg-clay" : isActive ? "bg-primary animate-pulse" : "bg-sage"}`} />
	                        <span className="text-xs font-black text-muted uppercase tracking-normal">
	                          {storyboardTaskStatusLabel(task.status)}
	                        </span>
	                      </div>
	                      <p className="text-sm text-ink/70 font-serif italic truncate">{task.brief}</p>
	                      <p className={`mt-1 text-xs font-bold tracking-normal ${isFailed ? "text-clay/80" : "text-muted"}`}>
	                        {task.error_message || task.message || "正在准备"}
	                      </p>
	                    </div>
	                    {task.status === "completed" && task.storyboard_id && (
	                      <button
	                        onClick={() => { handleLoadHistory(task.storyboard_id!); window.scrollTo({ top: 0, behavior: "smooth" }) }}
	                        className="shrink-0 text-xs text-primary/60 hover:text-primary font-black uppercase tracking-normal transition-colors"
	                      >
	                        查看
	                      </button>
	                    )}
	                  </div>
	                  <div className="mt-4 h-1.5 rounded-full bg-paper overflow-hidden">
	                    <div
	                      className={`h-full rounded-full transition-all duration-700 ${isFailed ? "bg-clay/70" : "bg-primary"}`}
	                      style={{ width: `${isFailed ? 100 : progressPct}%` }}
	                    />
	                  </div>
	                </div>
	              )
	            })}
	          </div>
	        </div>
	      )}

	      {!result && historyError && (
	        <div className="mb-6 flex items-center justify-between gap-4 px-4 py-3 rounded-lg bg-clay/5 border border-clay/15 text-xs text-clay">
	          <span>分镜历史读取失败：{historyError}</span>
	          <button type="button" onClick={() => void loadHistory()} className="shrink-0 font-bold hover:text-ink transition-colors">
	            重试
	          </button>
	        </div>
	      )}

	      {!result && history.length > 0 && (
        <section className="mb-10 border-y border-line/20 px-1 animate-in fade-in duration-500">
          <button
            type="button"
            onClick={() => setHistoryExpanded((previous) => !previous)}
            aria-expanded={historyExpanded}
            className="flex w-full items-center justify-between gap-4 py-4 text-left focus-visible:outline-2 focus-visible:outline-primary/35"
          >
            <span className="text-xs font-bold uppercase tracking-normal text-muted">创作历史</span>
            <span className="flex items-center gap-3 text-xs font-semibold text-muted">
              {history.length} 份脚本
              <span aria-hidden="true" className="text-primary">{historyExpanded ? "收起 ↑" : "展开 ↓"}</span>
            </span>
          </button>

          {historyExpanded && (
            <div className="border-t border-line/15 pb-5 pt-4">
              {history.length > 3 && (
                <div className="mb-3 flex justify-end">
                  <button
                    type="button"
                    onClick={() => setShowAllHistory((previous) => !previous)}
                    className="text-xs font-bold text-primary hover:text-ink focus-visible:outline-2 focus-visible:outline-primary/35"
                  >
                    {showAllHistory ? "只看最近 3 份" : "查看全部"}
                  </button>
                </div>
              )}
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                {visibleHistory.map((item) => (
                  <article key={item.id} className="perf-row group rounded-lg border border-line/20 bg-surface/45 p-4 hover:border-line/40">
                    <div className="mb-3 flex items-center justify-between">
                      <span className="text-xs font-bold text-muted">{item.shot_count} 镜</span>
                      <button
                        type="button"
                        onClick={() => handleDeleteHistory(item.id)}
                        className="-m-1 p-1 text-muted opacity-60 hover:text-clay group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-primary/35"
                        aria-label={`删除分镜历史 ${item.title}`}
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
                      </button>
                    </div>
                    <button
                      type="button"
                      onClick={() => { handleLoadHistory(item.id); window.scrollTo({ top: 0, behavior: "smooth" }) }}
                      className="block w-full rounded text-left focus-visible:outline-2 focus-visible:outline-primary/35"
                    >
                      <h4 className="mb-2 line-clamp-1 font-serif text-base font-medium text-ink group-hover:text-primary">{item.title}</h4>
                      <p className="mb-4 line-clamp-2 text-xs leading-relaxed text-muted">{item.brief}</p>
                      <span className="flex items-center justify-between border-t border-line/15 pt-3 text-xs font-semibold text-muted">
                        {item.total_duration_sec != null && Number.isFinite(item.total_duration_sec) ? item.total_duration_sec.toFixed(0) : "0"} 秒
                        <span className="text-primary">查看 →</span>
                      </span>
                    </button>
                  </article>
                ))}
              </div>
            </div>
          )}
        </section>
	      )}

      {/* Middle Section: Result Area */}
      <div className="min-h-[20rem]">
        {historyLoading ? (
          <div className="flex flex-col items-center justify-center py-48 gap-4">
            <div className="w-10 h-10 border-2 border-line/10 border-t-primary rounded-full animate-spin" />
            <span className="text-xs text-muted uppercase tracking-normal font-black">正在读取脚本</span>
          </div>
        ) : result ? (
          <div className="space-y-20 animate-in fade-in slide-in-from-bottom-8 duration-1000">
            <div className="flex items-center justify-between px-1">
              <div className="flex items-center gap-6">
                <span className="text-xs font-black text-muted uppercase tracking-normal">创作成果</span>
                <div className="h-px w-16 bg-line/10" />
              </div>
              <div className="flex items-center gap-8">
                <button 
                  onClick={() => setResult(null)}
                  className="text-xs text-muted hover:text-ink flex items-center gap-2 transition-all font-black uppercase tracking-normal"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>
                  返回历程
                </button>
                <button 
                  onClick={exportToMarkdown}
                  className="text-xs text-primary/60 hover:text-primary flex items-center gap-2 transition-all font-black uppercase tracking-normal hover:scale-105 active:scale-95"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
                  导出脚本
                </button>
              </div>
            </div>

            <JobCard
              title={result.title}
              status="completed"
              statusLabel="最终定稿"
              conclusion={result.full_notes || "分镜脚本已整理完毕。"}
            >
              <div className="flex items-center gap-16 text-xs text-muted font-black tracking-normal uppercase">
                <div className="flex flex-col gap-2">
                  <span className="text-muted">镜头单元</span>
                  <span className="text-ink text-2xl font-serif italic tracking-normal">{result.shots.length}</span>
                </div>
                <div className="w-px h-12 bg-line/5" />
                <div className="flex flex-col gap-2">
                  <span className="text-muted">预估时长</span>
                  <span className="text-ink text-2xl font-serif italic tracking-normal">{result.total_duration_sec}s</span>
                </div>
              </div>
            </JobCard>

            {/* Flattened Horizontal Result List - Compacted */}
            <div className="space-y-4">
              {result.shots.map((shot) => {
                const imageSrc = getStoryboardImageUrl(shot.image_url, shot.image_path)
                const imageStatus = retryingImageShots.has(shot.shot_number)
                  ? "generating"
                  : normalizeImageStatus(shot.image_status, imageSrc, shot.image_prompt)
                const canRetryImage = Boolean(result.id && shot.image_prompt && imageStatus !== "generating" && (!imageSrc || imageStatus === "failed"))
                return (
                  <div key={shot.shot_number} className="perf-row group flex flex-col md:flex-row gap-6 p-6 rounded-2xl bg-surface/40 hover:bg-surface transition-colors duration-500 border border-transparent hover:border-line/5 hover:shadow-sm">
                    {/* Left Metadata - Compact */}
                    <div className="w-full md:w-24 flex-shrink-0 flex md:flex-col items-center md:items-start justify-between md:justify-start gap-2">
                      <span className="text-4xl font-serif font-light text-ink/45 group-hover:text-primary transition-colors duration-500 leading-none">
                        {shot.shot_number < 10 ? `0${shot.shot_number}` : shot.shot_number}
                      </span>
                      <div className="flex flex-col gap-1">
                        <span className="text-xs font-black text-muted uppercase tracking-normal">{shot.duration_sec}s</span>
                        <span className="text-xs font-bold text-ink/60">{shot.camera_movement || "固定"}</span>
                        <StatusBean type="completed" label="定稿" />
                      </div>
                    </div>

                    {(imageSrc || shot.image_prompt) && (
                      <div className="w-full md:w-56 flex-shrink-0 space-y-2">
                        <div className="relative overflow-hidden rounded-xl bg-paper border border-line/5 aspect-video shadow-inner">
                          {imageSrc ? (
                            <img
                              src={imageSrc}
                              alt={`分镜 ${shot.shot_number} 预览`}
                              loading="lazy"
                              className="w-full h-full object-cover"
                            />
                          ) : (
                            <div className="w-full h-full flex flex-col items-center justify-center gap-2 px-4 text-center">
                              <span className={`w-2 h-2 rounded-full ${imageStatusDotClass(imageStatus)}`} />
                              <span className="text-xs text-muted font-bold tracking-normal">
                                {imageStatusLabel(imageStatus)}
                              </span>
                            </div>
                          )}
                        </div>
                        <div className="flex items-center justify-between gap-2">
                          <span className={`text-xs font-black uppercase tracking-normal ${imageStatusTextClass(imageStatus)}`}>
                            {imageStatusLabel(imageStatus)}
                          </span>
                          {canRetryImage && (
                            <button
                              type="button"
                              onClick={() => void handleRetryShotImage(shot.shot_number)}
                              className="text-xs text-primary/60 hover:text-primary font-black uppercase tracking-normal transition-colors"
                            >
                              重试生图
                            </button>
                          )}
                        </div>
                        {shot.image_error && imageStatus === "failed" && (
                          <p className="text-xs text-clay/65 leading-relaxed line-clamp-2">{shot.image_error}</p>
                        )}
                      </div>
                    )}

                    {/* Right Content Strip */}
                    <div className="flex-1 min-w-0 space-y-4">
                      <p className="text-lg text-ink leading-relaxed font-serif italic tracking-normal">{shot.description}</p>

                      {shot.image_prompt && (
                        <div className="p-4 bg-white/30 rounded-xl border border-line/5 shadow-inner group/prompt">
                          <div className="flex items-center gap-2 mb-2">
                            <div className="w-1 h-1 rounded-full bg-primary" />
                            <span className="text-xs font-black text-muted uppercase tracking-normal">生图提示词 / Visual Prompt</span>
                          </div>
                          <p className="text-xs text-muted leading-relaxed font-mono select-all italic line-clamp-1 group-hover/prompt:line-clamp-none transition-all">
                            {shot.image_prompt}
                          </p>
                        </div>
                      )}

                      <div className="flex items-center justify-between pt-2 border-t border-line/5">
                        {shot.bgm_note ? (
                          <div className="flex items-center gap-2 text-xs text-muted font-black uppercase tracking-normal">
                            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="opacity-30"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
                            {shot.bgm_note}
                          </div>
                        ) : <div />}
                        {shot.reference_from && (
                          <span className="text-xs text-muted font-serif italic truncate max-w-[200px]">
                            ~ {shot.reference_from}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-48 text-center">
            <Logo className="w-24 h-12 text-muted mb-10" />
            <p className="text-sm text-muted font-serif italic tracking-normal uppercase">等待灵感迸发</p>
          </div>
        )}
      </div>
    </div>
	  )
	}

function escapeMarkdownCell(value: string | number | null | undefined): string {
  const text = value == null || value === "" ? "-" : String(value)
  return text.replace(/\|/g, "\\|").replace(/\r?\n|\r/g, "<br>")
}

function createStoryboardTaskId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID()
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16)
    const next = char === "x" ? value : (value & 0x3) | 0x8
    return next.toString(16)
  })
}

function isStoryboardTaskActive(status: string): boolean {
  return ["queued", "collecting", "generating", "saving"].includes(status)
}

function storyboardTaskStatusLabel(status: string): string {
  if (status === "queued") return "排队中"
  if (status === "collecting") return "整理参考"
  if (status === "generating") return "生成中"
  if (status === "saving") return "保存中"
  if (status === "completed") return "已完成"
  if (status === "failed") return "失败"
  return status
}

function storyboardTasksEqual(a: StoryboardGenerationTask[], b: StoryboardGenerationTask[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) {
    const prev = a[i]
    const next = b[i]
    if (
      prev.id !== next.id ||
      prev.status !== next.status ||
      prev.progress !== next.progress ||
      prev.message !== next.message ||
      prev.storyboard_id !== next.storyboard_id ||
      prev.error_message !== next.error_message ||
      prev.updated_at !== next.updated_at
    ) {
      return false
    }
  }
  return true
}

function normalizeImageStatus(status: string | null | undefined, imageSrc: string | null, prompt?: string | null): string {
  if (status) return status
  if (imageSrc) return "completed"
  if (prompt) return "pending"
  return "skipped"
}

function imageStatusLabel(status: string): string {
  if (status === "pending") return "待生成"
  if (status === "generating") return "生成中"
  if (status === "completed") return "已生成"
  if (status === "failed") return "生成失败"
  if (status === "skipped") return "已跳过"
  return status
}

function imageStatusDotClass(status: string): string {
  if (status === "completed") return "bg-sage"
  if (status === "failed") return "bg-clay"
  if (status === "generating") return "bg-primary animate-pulse"
  return "bg-muted/25"
}

function imageStatusTextClass(status: string): string {
  if (status === "completed") return "text-sage/60"
  if (status === "failed") return "text-clay/65"
  if (status === "generating") return "text-primary/65"
  return "text-muted"
}

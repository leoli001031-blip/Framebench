import { useState, useEffect, useCallback, useRef } from "react"
import { listJobs, generateStoryboardStream, listStoryboards, getStoryboard, deleteStoryboard } from "@/lib/api"
import StatusBean from "@/components/StatusBean"
import JobCard from "@/components/JobCard"
import Logo from "@/components/Logo"
import { categorySort, downloadFile } from "@/lib/utils"
import type { JobInfo, StoryboardResult, StoryboardHistoryItem, StoryboardDetail } from "@/types"

export default function StoryboardPage() {
  const [jobs, setJobs] = useState<JobInfo[]>([])
  const [brief, setBrief] = useState(() => localStorage.getItem("storyboard_brief") || "")
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => {
    try { const raw = localStorage.getItem("storyboard_selected_ids"); return raw ? new Set(JSON.parse(raw)) : new Set() } catch { return new Set() }
  })
  const [targetDur, setTargetDur] = useState(() => localStorage.getItem("storyboard_target_dur") || "")
  const [generating, setGenerating] = useState(false)
  const [generationProgress, setGenerationProgress] = useState("")
  const [result, setResult] = useState<StoryboardResult | null>(null)
  const [error, setError] = useState("")
  const [history, setHistory] = useState<StoryboardHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [showAllHistory, setShowAllHistory] = useState(false)
  const generationAbortRef = useRef<AbortController | null>(null)

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
    try {
      setHistory(await listStoryboards())
    } catch (e) {
      console.error("Failed to load storyboard history", e)
      setError(e instanceof Error ? e.message : "分镜历史加载失败")
    }
  }, [])
  useEffect(() => {
    void Promise.resolve().then(async () => {
      await load()
      await loadHistory()
    })
  }, [load, loadHistory])
  useEffect(() => () => generationAbortRef.current?.abort(), [])
  useEffect(() => { localStorage.setItem("storyboard_brief", brief) }, [brief])
  useEffect(() => { localStorage.setItem("storyboard_target_dur", targetDur) }, [targetDur])
  useEffect(() => { localStorage.setItem("storyboard_selected_ids", JSON.stringify([...selectedIds])) }, [selectedIds])

  const referenceJobs = jobs.filter((j) => j.status === "completed")
  const visibleHistory = showAllHistory ? history : history.slice(0, 3)

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  // Group references by category
  const referenceGroups: Record<string, JobInfo[]> = {}
  referenceJobs.forEach(job => {
    const cat = job.category || "未分类"
    if (!referenceGroups[cat]) referenceGroups[cat] = []
    referenceGroups[cat].push(job)
  })

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
    if (selectedIds.size === 0) { setError("请选择参考素材"); return }
    setError("")
    setGenerating(true)
    setGenerationProgress("")
    generationAbortRef.current?.abort()
    const controller = new AbortController()
    generationAbortRef.current = controller
    const parsed = parseFloat(targetDur)
    const dur = !isNaN(parsed) ? Math.round(parsed) : undefined

    await generateStoryboardStream(brief, [...selectedIds], dur, {
      onProgress: (msg) => {
        if (!controller.signal.aborted) setGenerationProgress(msg)
      },
      onComplete: (res) => {
        if (controller.signal.aborted) return
        setResult(res)
        setGenerating(false)
        setGenerationProgress("")
        generationAbortRef.current = null
        loadHistory()
        localStorage.removeItem("storyboard_brief")
        localStorage.removeItem("storyboard_target_dur")
        localStorage.removeItem("storyboard_selected_ids")
      },
      onError: (msg) => {
        if (controller.signal.aborted) return
        setError(msg)
        setGenerating(false)
        setGenerationProgress("")
        generationAbortRef.current = null
      },
    }, controller.signal)
  }

  const handleLoadHistory = async (id: string) => {
    setHistoryLoading(true)
    try {
      const detail: StoryboardDetail = await getStoryboard(id)
      setResult({ title: detail.title, total_duration_sec: detail.total_duration_sec, shots: detail.shots, full_notes: detail.full_notes })
      // Sync state to left panel for 'Remix' mode
      setBrief(detail.brief)
      setTargetDur(detail.total_duration_sec?.toString() || "")
      setSelectedIds(new Set(detail.reference_job_ids))
    } catch { console.warn("handleLoadHistory failed") }
    finally { setHistoryLoading(false) }
  }

  const handleReset = () => {
    setBrief("")
    setTargetDur("")
    setSelectedIds(new Set())
    setResult(null)
    setError("")
    generationAbortRef.current?.abort()
    generationAbortRef.current = null
    setGenerating(false)
    setGenerationProgress("")
    localStorage.removeItem("storyboard_brief")
    localStorage.removeItem("storyboard_target_dur")
    localStorage.removeItem("storyboard_selected_ids")
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
    md += `| 镜号 | 时长 | 画面描述 | 机位 | 生图提示词 | 参考来源 |\n`
    md += `| :--- | :--- | :--- | :--- | :--- | :--- |\n`
    
    result.shots.forEach(s => {
      md += `| ${s.shot_number} | ${s.duration_sec}s | ${s.description} | ${s.camera_movement} | ${s.image_prompt || '-'} | ${s.reference_from} |\n`
    })
    
    md += `\n---\n`
    md += `*Generated by Framebench / 拉片工作台*\n`

    downloadFile(md, `${result.title.replace(/\s+/g, '_')}_分镜脚本.md`)
  }

  const handleDeleteHistory = async (id: string) => {
    if (!window.confirm("确定删除？")) return
    try { await deleteStoryboard(id); loadHistory() } catch (e) { setError(e instanceof Error ? e.message : "删除失败") }
  }

  return (
    <div className="max-w-6xl mx-auto px-4 pb-32">
      {/* Top Section: Horizontal Creation Bar - Visible only when not viewing a result */}
      {!result && (
        <div className="bg-surface rounded-3xl p-8 border border-line/10 shadow-[0_8px_30px_rgb(0,0,0,0.04)] mb-20 transition-all focus-within:shadow-[0_20px_50px_rgba(47,39,34,0.05)] animate-in fade-in slide-in-from-top-4 duration-500">
          <div className="flex flex-col md:flex-row gap-10 items-start">
            
            {/* Brief Input - Grows to fill */}
            <div className="flex-1 w-full space-y-4">
              <div className="flex items-center justify-between px-1">
                <h3 className="text-[10px] font-bold text-muted/30 uppercase tracking-[0.3em]">创作需求</h3>
                <button 
                  onClick={handleReset}
                  className="text-[10px] text-primary/40 hover:text-primary transition-colors flex items-center gap-1.5 font-black uppercase tracking-widest"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
                  重置
                </button>
              </div>
              <textarea
                value={brief}
                onChange={(e) => setBrief(e.target.value)}
                placeholder="在这里输入你的创作意图..."
                className="w-full h-28 bg-transparent text-xl text-ink placeholder:text-muted/15 resize-none focus:outline-none leading-relaxed font-serif italic"
              />
            </div>

            {/* Configuration & Action - Fixed width or compact */}
            <div className="w-full md:w-80 space-y-6 pt-1">
              <div className="space-y-4">
                <h3 className="text-[10px] font-bold text-muted/30 uppercase tracking-[0.3em]">执行设置</h3>
                <div className="flex flex-col gap-3">
                  {/* Duration & Generate Row */}
                  <div className="flex items-center gap-3">
                    <div className="flex-1 flex items-center gap-2 px-4 py-3 bg-paper rounded-2xl border border-line/10 focus-within:border-primary/20 transition-colors">
                      <span className="text-[10px] text-muted/40 uppercase font-black tracking-tighter">秒</span>
                      <input
                        type="number" min="1" step="1" value={targetDur}
                        onChange={(e) => setTargetDur(e.target.value)}
                        placeholder="时长"
                        className="w-full bg-transparent text-xs text-ink font-black focus:outline-none"
                      />
                    </div>
                    <button
                      onClick={handleGenerate} disabled={generating}
                      className={`flex-[2] py-3 rounded-2xl text-sm font-black uppercase tracking-widest shadow-lg transition-all ${
                        generating ? "bg-primary-soft text-muted cursor-not-allowed" : "bg-primary text-white hover:bg-primary/90 hover:scale-[1.03] active:scale-[0.97] shadow-primary/20"
                      }`}
                    >
                      {generating ? "正在生成" : "开始创作"}
                    </button>
                  </div>

                  {/* Dropdown Reference Selector */}
                  <div className="relative group/ref">
                    <button className="w-full flex items-center justify-between px-4 py-3 bg-paper rounded-2xl border border-line/10 text-[11px] text-ink font-bold hover:border-primary/20 transition-all uppercase tracking-widest text-left">
                      <span className="truncate">
                        {selectedIds.size > 0 ? `已选择 ${selectedIds.size} 个参考` : "选择参考素材"}
                      </span>
                      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="text-muted/20"><path d="m6 9 6 6 6-6"/></svg>
                    </button>
                    
                    {/* Dropdown Menu */}
                    <div className="absolute top-full left-0 right-0 mt-3 bg-surface border border-line/10 rounded-[2rem] shadow-2xl z-[100] opacity-0 invisible group-focus-within/ref:opacity-100 group-focus-within/ref:visible transition-all max-h-[26rem] overflow-y-auto p-6 custom-scrollbar backdrop-blur-xl bg-surface/95 text-left">
                      <div className="space-y-8">
                        {Object.entries(referenceGroups).sort(categorySort).map(([catName, items]) => {
                          const allSelected = items.every(j => selectedIds.has(j.id))
                          const someSelected = items.some(j => selectedIds.has(j.id)) && !allSelected
                          return (
                            <div key={catName} className="space-y-3">
                              <div className="flex items-center justify-between px-1 group/cat cursor-pointer" onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggleCategory(catName); }}>
                                <h4 className="text-[10px] font-black text-muted/40 uppercase tracking-[0.2em] group-hover/cat:text-primary transition-colors">{catName}</h4>
                                <div className={`w-3.5 h-3.5 rounded border border-line transition-all flex items-center justify-center ${allSelected ? "bg-primary border-primary shadow-[0_0_10px_rgba(122,85,60,0.3)]" : someSelected ? "bg-primary/30 border-primary/20" : "bg-white"}`}>
                                  {allSelected && <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="4" className="w-2.5 h-2.5"><polyline points="20 6 9 17 4 12"/></svg>}
                                  {someSelected && <div className="w-1.5 h-0.5 bg-white rounded-full" />}
                                </div>
                              </div>
                              <div className="space-y-1 ml-1">
                                {items.map(job => (
                                  <div 
                                    key={job.id} 
                                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggleSelect(job.id); }} 
                                    className={`px-3 py-2 rounded-xl text-[11px] cursor-pointer transition-all ${
                                      selectedIds.has(job.id) ? "bg-primary-soft/30 text-ink font-bold" : "text-muted/50 hover:bg-paper"
                                    }`}
                                  >
                                    {job.filename}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              {generating && generationProgress && (
                <div className="flex items-center gap-2 px-1 animate-in fade-in duration-300">
                  <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                  <p className="text-[10px] text-muted/40 font-bold tracking-wider text-left">{generationProgress}</p>
                </div>
              )}
              {error && <p className="text-[10px] text-clay px-1 font-bold italic animate-pulse text-left">● {error}</p>}
            </div>
          </div>
        </div>
      )}

      {/* Archives Section - Moved Up (Visible only when no result is shown) */}
      {!result && history.length > 0 && (
        <div className="mb-24 animate-in fade-in duration-1000 px-1">
          <div className="flex items-center justify-between mb-10">
            <div className="flex items-center gap-6">
              <h3 className="text-[10px] font-black text-muted/30 uppercase tracking-[0.5em]">历程 / 创作档案</h3>
              <div className="h-px w-20 bg-line/10" />
            </div>
            <div className="flex items-center gap-5">
              <span className="text-[10px] font-bold text-muted/20 uppercase tracking-widest">已保存 {history.length} 份脚本</span>
              {history.length > 3 && (
                <button
                  onClick={() => setShowAllHistory((prev) => !prev)}
                  className="text-[10px] font-black text-primary/45 hover:text-primary uppercase tracking-widest transition-colors"
                >
                  {showAllHistory ? "收起" : "更多"}
                </button>
              )}
            </div>
          </div>
          
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {visibleHistory.map((item) => (
              <div 
                key={item.id} 
                onClick={() => { handleLoadHistory(item.id); window.scrollTo({ top: 0, behavior: 'smooth' }); }}
                className="group bg-surface/20 border border-line/5 hover:border-line/20 p-8 rounded-[2.5rem] cursor-pointer transition-all duration-700 hover:shadow-[0_30px_60px_rgba(47,39,34,0.1)] hover:-translate-y-2"
              >
                <div className="flex items-center justify-between mb-6">
                  <span className="text-[10px] font-black text-muted/20 uppercase tracking-[0.2em]">{item.shot_count} 单元</span>
                  <button 
                    onClick={(e) => { e.stopPropagation(); handleDeleteHistory(item.id); }} 
                    className="p-2 -m-2 text-muted/5 hover:text-clay transition-colors opacity-0 group-hover:opacity-100"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
                  </button>
                </div>
                <h4 className="text-xl font-serif font-medium text-ink/80 group-hover:text-primary transition-colors line-clamp-1 mb-3 tracking-tight italic">{item.title}</h4>
                <p className="text-[11px] text-muted/40 line-clamp-2 leading-relaxed mb-6 italic font-serif">{item.brief}</p>
                <div className="flex items-center justify-between pt-6 border-t border-line/5">
                  <span className="text-[10px] text-muted/20 font-black uppercase tracking-widest">{item.total_duration_sec != null && Number.isFinite(item.total_duration_sec) ? item.total_duration_sec.toFixed(0) : "0"}s</span>
                  <div className="flex items-center gap-2 text-[10px] text-primary/40 font-black uppercase tracking-[0.2em] group-hover:tracking-[0.3em] transition-all">
                    查看
                    <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6"/></svg>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Middle Section: Result Area */}
      <div className="min-h-[20rem]">
        {historyLoading ? (
          <div className="flex flex-col items-center justify-center py-48 gap-4 opacity-50">
            <div className="w-10 h-10 border-2 border-line/10 border-t-primary rounded-full animate-spin" />
            <span className="text-[10px] text-muted/40 uppercase tracking-[0.4em] font-black">正在读取脚本</span>
          </div>
        ) : result ? (
          <div className="space-y-20 animate-in fade-in slide-in-from-bottom-8 duration-1000">
            <div className="flex items-center justify-between px-1">
              <div className="flex items-center gap-6">
                <span className="text-[10px] font-black text-muted/20 uppercase tracking-[0.4em]">创作成果</span>
                <div className="h-px w-16 bg-line/10" />
              </div>
              <div className="flex items-center gap-8">
                <button 
                  onClick={() => setResult(null)}
                  className="text-[10px] text-muted/30 hover:text-ink flex items-center gap-2 transition-all font-black uppercase tracking-widest"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>
                  返回历程
                </button>
                <button 
                  onClick={exportToMarkdown}
                  className="text-[10px] text-primary/60 hover:text-primary flex items-center gap-2 transition-all font-black uppercase tracking-[0.2em] hover:scale-105 active:scale-95"
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
              <div className="flex items-center gap-16 text-[10px] text-muted font-black tracking-[0.3em] uppercase">
                <div className="flex flex-col gap-2">
                  <span className="text-muted/15">镜头单元</span>
                  <span className="text-ink text-2xl font-serif italic tracking-normal">{result.shots.length}</span>
                </div>
                <div className="w-px h-12 bg-line/5" />
                <div className="flex flex-col gap-2">
                  <span className="text-muted/15">预估时长</span>
                  <span className="text-ink text-2xl font-serif italic tracking-normal">{result.total_duration_sec}s</span>
                </div>
              </div>
            </JobCard>

            {/* Flattened Horizontal Result List - Compacted */}
            <div className="space-y-4">
              {result.shots.map((shot) => (
                <div key={shot.shot_number} className="group flex flex-col md:flex-row gap-6 p-6 rounded-2xl bg-surface/40 hover:bg-surface transition-all duration-500 border border-transparent hover:border-line/5 hover:shadow-sm">
                  {/* Left Metadata - Compact */}
                  <div className="w-full md:w-24 flex-shrink-0 flex md:flex-col items-center md:items-start justify-between md:justify-start gap-2">
                    <span className="text-4xl font-serif font-light text-ink/20 group-hover:text-primary transition-colors duration-500 leading-none">
                      {shot.shot_number < 10 ? `0${shot.shot_number}` : shot.shot_number}
                    </span>
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-black text-muted/30 uppercase tracking-[0.2em]">{shot.duration_sec}s</span>
                      <StatusBean type="completed" label="定稿" />
                    </div>
                  </div>

                  {/* Right Content Strip */}
                  <div className="flex-1 min-w-0 space-y-4">
                    <p className="text-lg text-ink leading-relaxed font-serif italic tracking-tight">{shot.description}</p>
                    
                    {shot.image_prompt && (
                      <div className="p-4 bg-white/30 rounded-xl border border-line/5 shadow-inner group/prompt">
                        <div className="flex items-center gap-2 mb-2 opacity-20 group-hover/prompt:opacity-40 transition-opacity">
                          <div className="w-1 h-1 rounded-full bg-primary" />
                          <span className="text-[8px] font-black text-muted/60 uppercase tracking-[0.3em]">生图提示词 / Visual Prompt</span>
                        </div>
                        <p className="text-[10px] text-muted/40 leading-relaxed font-mono select-all italic line-clamp-1 group-hover/prompt:line-clamp-none transition-all">
                          {shot.image_prompt}
                        </p>
                      </div>
                    )}

                    <div className="flex items-center justify-between pt-2 border-t border-line/5">
                      {shot.bgm_note ? (
                        <div className="flex items-center gap-2 text-[9px] text-muted/30 font-black uppercase tracking-[0.2em]">
                          <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="opacity-30"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
                          {shot.bgm_note}
                        </div>
                      ) : <div />}
                      {shot.reference_from && (
                        <span className="text-[9px] text-muted/20 font-serif italic truncate max-w-[200px]">
                          ~ {shot.reference_from}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-48 text-center opacity-30">
            <Logo className="w-24 h-12 text-muted mb-10" />
            <p className="text-sm text-muted font-serif italic tracking-[0.3em] uppercase">等待灵感迸发</p>
          </div>
        )}
      </div>
    </div>
  )
}

import { useState, useEffect, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { listJobs, updateJob, deleteJob, listCategories } from "@/lib/api"
import StatusBean from "@/components/StatusBean"
import { mapJobStatus } from "@/lib/constants"
import EvidenceBar from "@/components/EvidenceBar"
import { categorySort } from "@/lib/utils"
import type { JobInfo } from "@/types"

export default function LibraryPage() {
  const [jobs, setJobs] = useState<JobInfo[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState("")
  const [activeCategory, setActiveCategory] = useState("全部")
  const [newCatOpen, setNewCatOpen] = useState(false)
  const [newCatName, setNewCatName] = useState("")
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoadError("")
    setLoading(true)
    try {
      const [jobsData, catsData] = await Promise.all([
        listJobs(),
        listCategories().catch((e) => { console.warn("listCategories failed", e); return { categories: [] } }),
      ])
      setJobs(jobsData)
      setCategories(catsData.categories)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "素材库加载失败")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void Promise.resolve().then(load)
  }, [load])

  const handleSetCategory = async (jobId: string, cat: string) => {
    await updateJob(jobId, cat ? { category: cat } : {})
    load()
  }

  const handleCreateCategory = () => {
    const name = newCatName.trim()
    if (!name || categories.includes(name)) { setNewCatOpen(false); setNewCatName(""); return }
    setCategories((prev) => [...prev, name].sort())
    setActiveCategory(name)
    setNewCatOpen(false)
    setNewCatName("")
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm("确定删除这个视频吗？")) return
    await deleteJob(id)
    load()
  }

  const handleDeleteCategory = async (cat: string) => {
    if (!window.confirm(`删除分类"${cat}"？所有视频将移入未分类。`)) return
    const targets = jobs.filter((j) => j.category === cat)
    await Promise.all(targets.map((j) => updateJob(j.id, { category: "" })))
    setCategories((prev) => prev.filter((c) => c !== cat))
    if (activeCategory === cat) setActiveCategory("全部")
  }

  const completedJobs = jobs.filter((j) => j.status === "completed")
  const filteredJobs = completedJobs.filter((j) => {
    if (activeCategory !== "全部" && j.category !== activeCategory) return false
    return true
  })

  const groups: Record<string, JobInfo[]> = {}
  filteredJobs.forEach(job => {
    const cat = job.category || "未分类"
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(job)
  })

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center mb-8 border-b border-line/10 pb-6 px-1">
        <div className="flex items-center gap-6">
          {["全部", ...categories].map((cat) => (
            <span key={cat} className="group relative flex items-center gap-1">
              <button
                onClick={() => setActiveCategory(cat)}
                className={`text-[11px] font-bold uppercase tracking-[0.2em] transition-all relative py-2 ${
                  activeCategory === cat
                    ? "text-ink after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-primary after:rounded-full"
                    : "text-muted/30 hover:text-ink transition-colors"
                }`}
              >
                {cat}
              </button>
              {cat !== "全部" && (
                <button
                  onClick={(e) => { e.stopPropagation(); handleDeleteCategory(cat) }}
                  className="text-[10px] text-muted/10 hover:text-clay transition-colors opacity-0 group-hover:opacity-100 -ml-0.5"
                >
                  ×
                </button>
              )}
            </span>
          ))}
          {newCatOpen ? (
            <input
              value={newCatName}
              onChange={(e) => setNewCatName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleCreateCategory(); if (e.key === "Escape") { setNewCatOpen(false); setNewCatName("") } }}
              onBlur={() => { setNewCatOpen(false); setNewCatName("") }}
              placeholder="分类名"
              className="text-[10px] px-2 py-1 w-20 bg-white border border-line rounded focus:outline-none focus:border-primary"
              autoFocus
            />
          ) : (
            <button
              onClick={() => setNewCatOpen(true)}
              className="text-[10px] text-muted/20 hover:text-primary transition-colors font-bold"
            >
              + 分类
            </button>
          )}
        </div>
      </div>

      {loading && (
        <div className="py-48 text-center">
          <p className="text-sm text-muted/20 font-serif italic tracking-widest animate-pulse">正在读取仓库...</p>
        </div>
      )}

      {loadError && (
        <div className="py-32 text-center">
          <p className="text-sm text-clay font-serif italic tracking-widest mb-4">● {loadError}</p>
          <button onClick={load} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold">重试</button>
        </div>
      )}

      {!loading && !loadError && jobs.length === 0 && (
        <div className="py-48 text-center">
          <p className="text-sm text-muted/20 font-serif italic tracking-widest">暂无素材存入仓库。</p>
        </div>
      )}

      {!loading && !loadError && <div className="space-y-12 mb-32">
        {Object.entries(groups).sort(categorySort).map(([catName, items]) => (
          <div key={catName} className="space-y-8 animate-in fade-in slide-in-from-bottom-2 duration-700">
            <div className="flex items-baseline gap-4 px-1">
              <h3 className="text-2xl font-serif font-light text-ink/20 tracking-tighter">{catName}</h3>
              <div className="h-px flex-1 bg-line/5" />
              <span className="text-[10px] font-bold text-muted/20 uppercase tracking-widest">{items.length} 单元</span>
            </div>

            <div className="space-y-2">
              {items.map((job) => (
                <EvidenceBar
                  key={job.id}
                  name={job.filename}
                  type={job.category || "常规"}
                  status={<StatusBean type={mapJobStatus(job.status)} />}
                  time={job.total_shots ? `${job.total_shots} 镜头` : undefined}
                  onClick={() => navigate(`/jobs/${job.id}`)}
                  className="hover:bg-surface/80 hover:shadow-[0_10px_30px_-5px_rgba(47,39,34,0.05)] py-4 rounded-xl border border-transparent hover:border-line/5 transition-all duration-500"
                  action={
                    <div className="flex items-center gap-6" onClick={(e) => e.stopPropagation()}>
                      <select
                        value={job.category || ""}
                        onChange={(e) => handleSetCategory(job.id, e.target.value)}
                        className="text-[10px] bg-transparent border border-line/20 rounded px-2 py-1 text-muted/50 focus:outline-none focus:border-primary cursor-pointer"
                      >
                        <option value="">未分类</option>
                        {categories.map((cat) => (
                          <option key={cat} value={cat}>{cat}</option>
                        ))}
                      </select>
                      <button
                        onClick={() => navigate(`/jobs/${job.id}/report`)}
                        className="text-[10px] text-sage/40 hover:text-sage transition-colors font-black uppercase tracking-widest"
                      >
                        报告
                      </button>
                      <button
                        onClick={() => handleDelete(job.id)}
                        className="text-[10px] text-muted/20 hover:text-clay transition-colors font-bold uppercase tracking-widest"
                      >
                        移除
                      </button>
                    </div>
                  }
                />
              ))}
            </div>
          </div>
        ))}
      </div>}
    </div>
  )
}

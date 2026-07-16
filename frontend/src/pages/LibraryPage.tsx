import { useState, useEffect, useCallback, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import { listJobs, updateJob, deleteJob, restoreJob, permanentlyDeleteJob, listCategories, renameCategory } from "@/lib/api"
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
  const [actionError, setActionError] = useState("")
  const [activeCategory, setActiveCategory] = useState("全部")
  const [showTrash, setShowTrash] = useState(false)
  const [search, setSearch] = useState("")
  const [newCatOpen, setNewCatOpen] = useState(false)
  const [newCatName, setNewCatName] = useState("")
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoadError("")
    setActionError("")
    setLoading(true)
    try {
      const [jobsData, catsData] = await Promise.all([
        listJobs(showTrash ? { onlyDeleted: true } : {}),
        listCategories().catch((e) => { console.warn("listCategories failed", e); return { categories: [] } }),
      ])
      setJobs(jobsData)
      setCategories(catsData.categories)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "素材库加载失败")
    } finally {
      setLoading(false)
    }
  }, [showTrash])

  useEffect(() => {
    void Promise.resolve().then(load)
  }, [load])

  const handleSetCategory = async (jobId: string, cat: string) => {
    await updateJob(jobId, { category: cat })
    load()
  }

  const handleCreateCategory = () => {
    const name = newCatName.trim()
    if (!name || categories.includes(name)) { setNewCatOpen(false); setNewCatName(""); return }
    setCategories((prev) => [...prev, name].sort())
    setActiveCategory("全部")
    setNewCatOpen(false)
    setNewCatName("")
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm("确定将这个视频移入回收站吗？分析文件会保留，可在回收站恢复。")) return
    await deleteJob(id)
    load()
  }

  const handleRestore = async (id: string) => {
    await restoreJob(id)
    load()
  }

  const handlePermanentDelete = async (job: JobInfo) => {
    const shots = job.total_shots ? `，${job.total_shots} 个镜头` : ""
    if (!window.confirm(`永久删除“${job.filename}”${shots}？这会删除数据库记录和本地文件，无法撤销。`)) return
    await permanentlyDeleteJob(job.id)
    load()
  }

  const handleDeleteCategory = async (cat: string) => {
    if (!window.confirm(`删除分类"${cat}"？所有视频将移入未分类。`)) return
    await renameCategory(cat, "未分类")
    setCategories((prev) => prev.filter((c) => c !== cat))
    if (activeCategory === cat) setActiveCategory("全部")
  }

  const handleRenameCategory = async (cat: string) => {
    const nextName = window.prompt("输入新的分类名。若输入已有分类名，将合并到该分类。", cat)?.trim()
    if (!nextName || nextName === cat) return
    try {
      await renameCategory(cat, nextName)
      setActiveCategory(nextName)
      await load()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "分类重命名失败")
    }
  }

  const duplicateCounts = useMemo(() => {
    const counts = new Map<string, number>()
    jobs.forEach((job) => {
      if (!job.deleted_at) counts.set(job.filename, (counts.get(job.filename) || 0) + 1)
    })
    return counts
  }, [jobs])

  const groups = useMemo(() => {
    const next: Record<string, JobInfo[]> = {}
    const keyword = search.trim().toLowerCase()
    jobs
      .filter((j) => showTrash || activeCategory === "全部" || (j.category || "未分类") === activeCategory)
      .filter((j) => !keyword || j.filename.toLowerCase().includes(keyword) || (j.category || "未分类").toLowerCase().includes(keyword))
      .forEach((job) => {
        const cat = showTrash ? "回收站" : job.category || "未分类"
        if (!next[cat]) next[cat] = []
        next[cat].push(job)
      })
    return next
  }, [activeCategory, jobs, search, showTrash])

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center justify-between gap-6 mb-8 border-b border-line/10 pb-6 px-1">
        <div className="flex items-center gap-4">
          <button
            onClick={() => setShowTrash(false)}
            className={`text-xs font-bold uppercase tracking-normal transition-all relative py-2 ${
              !showTrash ? "text-ink after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-primary after:rounded-full" : "text-muted hover:text-ink"
            }`}
          >
            素材
          </button>
          <button
            onClick={() => setShowTrash(true)}
            className={`text-xs font-bold uppercase tracking-normal transition-all relative py-2 ${
              showTrash ? "text-ink after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-primary after:rounded-full" : "text-muted hover:text-ink"
            }`}
          >
            回收站
          </button>
        </div>

        {!showTrash && (
          <div className="flex items-center gap-6 min-w-0 overflow-x-auto">
            {["全部", ...categories].map((cat) => (
              <span key={cat} className="group relative flex items-center gap-1">
                <button
                  onClick={() => setActiveCategory(cat)}
                  className={`text-xs font-bold uppercase tracking-normal transition-all relative py-2 whitespace-nowrap ${
                    activeCategory === cat
                      ? "text-ink after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-primary after:rounded-full"
                      : "text-muted hover:text-ink transition-colors"
                  }`}
                >
                  {cat}
                </button>
                {cat !== "全部" && (
                  <span className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={(e) => { e.stopPropagation(); void handleRenameCategory(cat) }}
                      className="text-xs text-muted hover:text-primary transition-colors"
                    >
                      改
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDeleteCategory(cat) }}
                      className="text-xs text-muted hover:text-clay transition-colors"
                    >
                      ×
                    </button>
                  </span>
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
                className="text-xs px-2 py-1 w-20 bg-white border border-line rounded focus:outline-none focus:border-primary"
                autoFocus
              />
            ) : (
              <button
                onClick={() => setNewCatOpen(true)}
                className="text-xs text-muted hover:text-primary transition-colors font-bold whitespace-nowrap"
              >
                + 分类
              </button>
            )}
          </div>
        )}
      </div>

      {!showTrash && (
        <div className="mb-8 flex items-center gap-3 px-1">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索文件名或分类"
            className="w-full bg-surface/50 border border-line/10 rounded-xl px-4 py-3 text-xs text-ink placeholder:text-muted focus:outline-none focus:border-primary/20 focus:bg-white transition-all"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="shrink-0 text-xs text-muted hover:text-ink font-bold uppercase tracking-normal"
            >
              清空
            </button>
          )}
        </div>
      )}

      {actionError && (
        <div className="mb-6 px-4 py-3 rounded-xl bg-clay/5 border border-clay/10 text-xs text-clay font-bold">
          {actionError}
        </div>
      )}

      {loading && (
        <div className="py-48 text-center">
          <p className="text-sm text-muted font-serif italic tracking-normal animate-pulse">{showTrash ? "正在读取回收站..." : "正在读取仓库..."}</p>
        </div>
      )}

      {loadError && (
        <div className="py-32 text-center">
          <p className="text-sm text-clay font-serif italic tracking-normal mb-4">● {loadError}</p>
          <button onClick={load} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold">重试</button>
        </div>
      )}

      {!loading && !loadError && Object.keys(groups).length === 0 && (
        <div className="py-48 text-center">
          <p className="text-sm text-muted font-serif italic tracking-normal">
            {showTrash
              ? "回收站是空的。"
              : search || activeCategory !== "全部"
                ? "没有匹配的素材。"
                : "暂无素材存入仓库。"}
          </p>
          {!showTrash && (search || activeCategory !== "全部") && (
            <button
              type="button"
              onClick={() => { setSearch(""); setActiveCategory("全部") }}
              className="mt-4 text-xs font-bold text-primary hover:text-primary/80 transition-colors"
            >
              清空筛选
            </button>
          )}
        </div>
      )}

      {!loading && !loadError && Object.keys(groups).length > 0 && <div className="space-y-12 mb-32">
        {Object.entries(groups).sort(categorySort).map(([catName, items]) => (
          <div key={catName} className="space-y-8 animate-in fade-in slide-in-from-bottom-2 duration-700">
            <div className="flex items-baseline gap-4 px-1">
              <h3 className="text-2xl font-serif font-light text-ink/55 tracking-normal">{catName}</h3>
              <div className="h-px flex-1 bg-line/5" />
              <span className="text-xs font-bold text-muted uppercase tracking-normal">{items.length} 单元</span>
            </div>

            <div className="space-y-2">
              {items.map((job) => (
                <EvidenceBar
                  key={job.id}
                  name={job.filename}
                  type={duplicateCounts.get(job.filename)! > 1 ? `${job.category || "常规"} · 重复` : job.category || "常规"}
                  status={<StatusBean type={mapJobStatus(job.status)} />}
                  time={[
                    job.total_shots ? `${job.total_shots} 镜头` : "",
                    formatDate(job.created_at),
                  ].filter(Boolean).join(" / ")}
                  onClick={showTrash ? undefined : () => navigate(job.status === "completed" ? `/jobs/${job.id}/report` : `/jobs/${job.id}`)}
                  className="perf-row-sm hover:bg-surface/80 hover:shadow-[0_10px_30px_-5px_rgba(47,39,34,0.05)] py-4 rounded-xl border border-transparent hover:border-line/5 transition-colors duration-500"
                  action={
                    <div className="flex items-center gap-6" onClick={(e) => e.stopPropagation()}>
                      {showTrash ? (
                        <>
                          <button
                            onClick={() => { setActionError(""); handleRestore(job.id).catch((e) => setActionError(e instanceof Error ? e.message : "恢复失败")) }}
                            className="text-xs text-sage/60 hover:text-sage transition-colors font-black uppercase tracking-normal"
                          >
                            恢复
                          </button>
                          <button
                            onClick={() => { setActionError(""); handlePermanentDelete(job).catch((e) => setActionError(e instanceof Error ? e.message : "永久删除失败")) }}
                            className="text-xs text-clay hover:text-ink transition-colors font-bold uppercase tracking-normal"
                          >
                            永久删除
                          </button>
                        </>
                      ) : (
                        <>
                          <select
                            value={job.category || ""}
                            onChange={(e) => handleSetCategory(job.id, e.target.value)}
                            className="text-xs bg-transparent border border-line/20 rounded px-2 py-1 text-muted focus:outline-none focus:border-primary cursor-pointer"
                          >
                            <option value="">未分类</option>
                            {categories.map((cat) => (
                              <option key={cat} value={cat}>{cat}</option>
                            ))}
                          </select>
                          {job.status === "completed" ? (
                            <button
                              onClick={() => navigate(`/jobs/${job.id}/report`)}
                              className="text-xs text-sage hover:text-primary transition-colors font-black uppercase tracking-normal"
                            >
                              报告
                            </button>
                          ) : (
                            <button
                              onClick={() => navigate(`/jobs/${job.id}`)}
                              className="text-xs text-primary hover:text-ink transition-colors font-black uppercase tracking-normal"
                            >
                              查看进度
                            </button>
                          )}
                          <button
                            onClick={() => { setActionError(""); handleDelete(job.id).catch((e) => setActionError(e instanceof Error ? e.message : "移入回收站失败")) }}
                            className="text-xs text-muted hover:text-clay transition-colors font-bold uppercase tracking-normal"
                          >
                            移除
                          </button>
                        </>
                      )}
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

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  return `${date.getMonth() + 1}/${date.getDate()}`
}

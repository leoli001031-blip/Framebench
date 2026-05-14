import { useState, useEffect, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { uploadVideo, listJobs, deleteJob, updateJob } from "@/lib/api"
import StatusBean from "@/components/StatusBean"
import { mapJobStatus } from "@/lib/constants"
import EvidenceBar from "@/components/EvidenceBar"
import CategoryInput from "@/components/CategoryInput"
import type { JobInfo } from "@/types"

export default function UploadPage() {
  const [jobs, setJobs] = useState<JobInfo[]>([])
  const [uploading, setUploading] = useState(false)
  const [loadingJobs, setLoadingJobs] = useState(true)
  const [loadError, setLoadError] = useState("")
  const [error, setError] = useState("")
  const [dragOver, setDragOver] = useState(false)
  const navigate = useNavigate()

  const loadJobs = useCallback(async () => {
    setLoadError("")
    setLoadingJobs(true)
    try {
      setJobs(await listJobs())
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "最近分析加载失败")
    } finally {
      setLoadingJobs(false)
    }
  }, [])

  useEffect(() => {
    void Promise.resolve().then(loadJobs)
  }, [loadJobs])

  const handleFile = async (file: File) => {
    if (!file.name.toLowerCase().match(/\.(mp4|mov|mkv|avi|webm)$/)) {
      setError("仅支持 mp4, mov, mkv, avi, webm 格式")
      return
    }
    setError("")
    setUploading(true)
    try {
      const res = await uploadVideo(file)
      navigate(`/jobs/${res.job_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败")
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm("确定移除？")) return
    try {
      await deleteJob(id)
      loadJobs()
    } catch (e) {
      setError(e instanceof Error ? e.message : "移除失败")
    }
  }

  return (
    <div className="max-w-3xl mx-auto animate-in fade-in duration-700">
      {/* Primary Action Area */}

      <div
        className={`rounded-xl p-12 text-center cursor-pointer transition-all mb-12 border-2 border-dashed ${
          dragOver ? "border-primary bg-primary-soft/30" : "border-line/40 hover:border-primary/40 bg-surface/50"
        }`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]) }}
        onClick={() => document.getElementById("file-input")?.click()}
      >
        <input
          id="file-input"
          type="file"
          accept="video/*"
          className="hidden"
          onChange={(e) => { if (e.target.files?.[0]) handleFile(e.target.files[0]) }}
        />
        <div className="flex flex-col items-center">
          <div className="w-12 h-12 bg-primary-soft rounded-full flex items-center justify-center text-primary mb-4">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>
          </div>
          <h2 className="text-lg font-medium text-ink mb-1">
            {uploading ? "正在整理..." : "上传视频"}
          </h2>
          <p className="text-sm text-muted/60">
            MP4 / MOV / MKV · 最大 2GB
          </p>
        </div>
      </div>

      {error && (
        <div className="mb-6 px-4 py-2 bg-clay/10 border border-clay/20 rounded text-clay text-xs flex items-center gap-2">
          <span>●</span> {error}
        </div>
      )}

      {loadingJobs && (
        <div className="py-8 text-center text-xs text-muted/30 animate-pulse">正在读取最近分析...</div>
      )}

      {loadError && (
        <div className="mb-6 px-4 py-3 bg-clay/10 border border-clay/20 rounded text-clay text-xs flex items-center justify-between gap-3">
          <span>● {loadError}</span>
          <button onClick={loadJobs} className="font-bold hover:text-ink transition-colors">重试</button>
        </div>
      )}

      {/* History Evidence Bars */}
      {!loadingJobs && !loadError && jobs.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center justify-between px-1">
            <h3 className="text-xs font-medium text-muted/80 tracking-wider uppercase">最近分析</h3>
            <span className="text-[10px] text-muted/40">{jobs.length} 项资源</span>
          </div>
          <div className="space-y-1">
            {jobs.map((job) => (
              <EvidenceBar
                key={job.id}
                type="视频"
                name={job.filename}
                status={<StatusBean type={mapJobStatus(job.status)} />}
                time={job.total_shots ? `${job.total_shots} 镜` : undefined}
                onClick={() => navigate(job.status === "completed" ? `/jobs/${job.id}/report` : `/jobs/${job.id}`)}
                icon={
                  <div className="w-8 h-8 bg-surface border border-line/30 rounded flex items-center justify-center text-muted">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><polyline points="11 3 11 11 14 8 17 11 17 3"/></svg>
                  </div>
                }
                action={
                  <div className="flex items-center gap-3" onClick={(e) => e.stopPropagation()}>
                    {job.status === "completed" && (
                        <CategoryInput
                          category={job.category || ""}
                          onSave={async (val) => { await updateJob(job.id, val ? { category: val } : {}); loadJobs() }}
                        />
                      )}
                    <button
                      onClick={() => handleDelete(job.id)}
                      className="text-[10px] text-muted hover:text-clay transition-colors"
                    >
                      移除
                    </button>
                  </div>
                }
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

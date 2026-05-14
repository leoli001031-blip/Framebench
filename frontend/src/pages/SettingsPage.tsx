import { useState, useEffect, useCallback } from "react"
import { listSettings, updateSetting, testConnectivity } from "@/lib/api"
import type { SystemSettingResponse } from "@/types"
import JobCard from "@/components/JobCard"

export default function SettingsPage() {
  const [settings, setSettings] = useState<SystemSettingResponse[]>([])
  const [localValues, setLocalValues] = useState<Record<string, string>>({})
  const [clearedSecrets, setClearedSecrets] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [sectionSuccess, setSectionSuccess] = useState<string | null>(null)
  const [testStatus, setTestStatus] = useState<{ type: "success" | "error" | "testing" | null; msg: string }>({ type: null, msg: "" })
  const [saveError, setSaveError] = useState("")
  const [loadError, setLoadError] = useState("")

  const loadSettings = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true)
    setLoadError("")
    try {
      const data = await listSettings()
      setSettings(data)
      const vals: Record<string, string> = {}
      data.forEach(s => vals[s.key] = s.is_secret ? "" : s.value || "")
      setLocalValues(vals)
      setClearedSecrets(new Set())
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "配置读取失败")
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void Promise.resolve().then(() => loadSettings())
  }, [loadSettings])

  const handleSaveSection = async (label: string, keys: string[]) => {
    setSavingKey(label)
    setSaveError("")
    setSectionSuccess(null)
    try {
      const updates = keys.flatMap((key) => {
        const setting = settings.find(item => item.key === key)
        const value = localValues[key] || ""
        if (!setting?.is_secret) return [updateSetting(key, value)]
        if (clearedSecrets.has(key)) return [updateSetting(key, "")]
        if (value.trim() === "") return []
        return [updateSetting(key, value)]
      })
      await Promise.all(updates)
      await loadSettings(false)
      setSectionSuccess(label)
      setTimeout(() => setSectionSuccess(null), 3000)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "保存失败")
    } finally {
      setSavingKey(null)
    }
  }

  const handleTest = async () => {
    setTestStatus({ type: "testing", msg: "正在测试连接..." })
    try {
      const res = await testConnectivity()
      setTestStatus({ type: "success", msg: res.message })
    } catch (e) {
      setTestStatus({ type: "error", msg: e instanceof Error ? e.message : "连接测试失败" })
    }
  }

  if (loading) return <div className="py-20 text-center opacity-30 animate-pulse">正在读取配置...</div>

  if (loadError) {
    return (
      <div className="py-24 text-center space-y-4">
        <p className="text-sm text-clay font-serif italic">● {loadError}</p>
        <button onClick={() => void loadSettings()} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold">重试</button>
      </div>
    )
  }

  const analysisKeys = ["analysis_api_key", "analysis_model", "analysis_base_url"]
  const storyboardKeys = ["storyboard_api_key", "storyboard_model", "storyboard_base_url"]

  const renderFields = (keys: string[]) => (
    <div className="space-y-6">
      {keys.map((key) => {
        const s = settings.find(item => item.key === key)
        if (!s) return null

        let label = s.description || s.key
        if (key.includes("api_key")) label = "API 密钥"
        if (key.includes("model")) label = "模型标识"
        if (key.includes("base_url")) label = "接口地址"
        const hasSavedSecret = s.is_secret && Boolean(s.value)
        const isCleared = clearedSecrets.has(key)

        return (
          <div key={key} className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between gap-3">
              <label className="text-[10px] font-bold text-muted/40 uppercase tracking-wider">
                {label}
              </label>
              {hasSavedSecret && (
                <span className={`text-[10px] font-bold tracking-wider ${isCleared ? "text-clay" : "text-muted/45"}`}>
                  {isCleared ? "保存后清空" : `已保存：${s.value}`}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <input
                type={s.is_secret ? "password" : "text"}
                value={localValues[key] || ""}
                onChange={(e) => {
                  setClearedSecrets(prev => {
                    const next = new Set(prev)
                    next.delete(key)
                    return next
                  })
                  setLocalValues(prev => ({ ...prev, [key]: e.target.value }))
                }}
                className="w-full bg-paper/50 border border-line/10 rounded-xl px-4 py-3 text-sm text-ink focus:outline-none focus:border-primary/20 focus:bg-white transition-all shadow-inner font-mono"
                placeholder={s.is_secret ? (hasSavedSecret ? "留空保留当前密钥，输入新密钥以替换" : "请输入 API 密钥") : "请输入..."}
              />
              {hasSavedSecret && (
                <button
                  type="button"
                  onClick={() => {
                    setLocalValues(prev => ({ ...prev, [key]: "" }))
                    setClearedSecrets(prev => {
                      const next = new Set(prev)
                      if (next.has(key)) next.delete(key)
                      else next.add(key)
                      return next
                    })
                  }}
                  className={`shrink-0 px-3 py-3 rounded-xl text-[10px] font-bold tracking-wider border transition-all ${
                    isCleared
                      ? "border-clay/30 text-clay bg-clay/5"
                      : "border-line/20 text-muted hover:text-ink hover:border-line/40"
                  }`}
                >
                  {isCleared ? "撤销" : "清空"}
                </button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )

  const renderFooter = (label: string, keys: string[]) => {
    const isSaving = savingKey === label
    const justSaved = sectionSuccess === label
    return (
      <div className="mt-6 pt-4 border-t border-line/5 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          {justSaved && (
            <span className="text-[10px] text-sage font-bold tracking-wider animate-in fade-in shrink-0">
              ● 已保存
            </span>
          )}
          {saveError && isSaving === false && (
            <span className="text-[10px] text-clay font-bold tracking-wider truncate">{saveError}</span>
          )}
          {testStatus.msg && (
            <span className={`text-[10px] font-bold tracking-wider truncate ${
              testStatus.type === "success" ? "text-sage" :
              testStatus.type === "error" ? "text-clay" :
              "text-muted"
            }`}>
              {testStatus.type === "testing" ? "..." : ""} {testStatus.msg}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handleTest}
            disabled={savingKey !== null || testStatus.type === "testing"}
            className="px-3 py-1.5 rounded-lg text-[10px] font-bold tracking-wider border border-line/30 text-muted hover:text-ink hover:border-line/50 transition-all disabled:opacity-30"
          >
            测试
          </button>
          <button
            onClick={() => handleSaveSection(label, keys)}
            disabled={isSaving}
            className={`px-4 py-1.5 rounded-lg text-[10px] font-bold tracking-wider transition-all ${
              isSaving ? "bg-primary-soft text-muted cursor-wait" : "bg-primary text-white hover:bg-primary/90 active:scale-95"
            }`}
          >
            {isSaving ? "保存中" : "保存"}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto space-y-12 pb-32">
      <div className="space-y-2 px-1 text-center">
        <h2 className="text-2xl font-serif font-light text-ink tracking-tighter italic">系统设置</h2>
        <p className="text-xs text-muted/40 font-serif italic">
          配置 AI 引擎密钥与模型参数
        </p>
      </div>

      <div className="space-y-8">
        <JobCard
          title="拉片分析引擎"
          status="completed"
          statusLabel="分析与识别"
          conclusion="逐镜头画面分析、技法识别与全片综述生成。"
        >
          {renderFields(analysisKeys)}
          {renderFooter("analysis", analysisKeys)}
        </JobCard>

        <JobCard
          title="分镜创作引擎"
          status="completed"
          statusLabel="创作与生成"
          conclusion="基于参考片风格迁移，生成创意分镜脚本与生图提示词。"
        >
          {renderFields(storyboardKeys)}
          {renderFooter("storyboard", storyboardKeys)}
        </JobCard>
      </div>

      <div className="pt-8 border-t border-line/5 opacity-20">
        <p className="text-[9px] text-center text-muted font-bold uppercase tracking-[0.4em]">
          Framebench System Registry v1.1
        </p>
      </div>
    </div>
  )
}
